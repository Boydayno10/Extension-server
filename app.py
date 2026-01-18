import os
import time
import mimetypes
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, abort, request
from flask_cors import CORS

_here = Path(__file__).resolve().parent
# Prefer loading `flask_server/.env` regardless of current working directory.
load_dotenv(dotenv_path=_here / ".env")
# Fallback to default discovery (useful if user keeps .env at repo root).
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET") or "web"
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or ""
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""

FLASK_HOST = os.getenv("FLASK_HOST") or "127.0.0.1"
FLASK_PORT = int(os.getenv("FLASK_PORT") or "5000")
FLASK_DEBUG = (os.getenv("FLASK_DEBUG") or "").strip() in {"1", "true", "True", "yes", "YES"}

HTML_CACHE_TTL_SECONDS = int(os.getenv("HTML_CACHE_TTL_SECONDS") or os.getenv("CACHE_TTL_SECONDS") or "30")
STATIC_CACHE_TTL_SECONDS = int(os.getenv("STATIC_CACHE_TTL_SECONDS") or "86400")
LOCAL_FALLBACK_DIR = os.getenv("LOCAL_FALLBACK_DIR")  # optional


def _cache_control_for_path(path: str) -> str:
    p = (path or "").lower()
    # HTML should stay relatively fresh during development.
    if p.endswith(".html") or p.endswith(".htm"):
        return f"public, max-age={HTML_CACHE_TTL_SECONDS}"

    # JSON config might change; keep shorter.
    if p.endswith(".json"):
        return f"public, max-age={min(HTML_CACHE_TTL_SECONDS, 300)}"

    # Static assets: cache aggressively so refreshes are consistently fast.
    if p.endswith((
        ".css",
        ".js",
        ".mjs",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".map",
    )):
        return f"public, max-age={STATIC_CACHE_TTL_SECONDS}, immutable"

    return f"public, max-age={min(HTML_CACHE_TTL_SECONDS, 300)}"


def _ttl_seconds_for_path(path: str) -> int:
    p = (path or "").lower()
    if p.endswith((".html", ".htm")):
        return HTML_CACHE_TTL_SECONDS
    if p.endswith(".json"):
        return min(HTML_CACHE_TTL_SECONDS, 300)
    if p.endswith((
        ".css",
        ".js",
        ".mjs",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".map",
    )):
        return STATIC_CACHE_TTL_SECONDS
    return min(HTML_CACHE_TTL_SECONDS, 300)


def _supabase_rest_insert(*, table: str, payload: dict) -> bool:
    """Insert a single row into a Supabase Postgres table via PostgREST.

    Uses the service role key from the server environment (never exposed to the browser).
    """

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return False

    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=8)
    except requests.RequestException:
        return False

    return 200 <= resp.status_code < 300


def _supabase_rest_insert_debug(*, table: str, payload: dict) -> tuple[bool, int, str]:
    """Same insert as _supabase_rest_insert, but returns details for debugging."""

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return (False, 0, "Supabase not configured (missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY)")

    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=8)
    except requests.RequestException as e:
        return (False, 0, f"Request failed: {type(e).__name__}")

    ok = 200 <= resp.status_code < 300
    msg = "OK" if ok else (resp.text[:300] if resp.text else f"HTTP {resp.status_code}")
    return (ok, resp.status_code, msg)


def _read_json_body_safely() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data

    raw = request.get_data(cache=False, as_text=True) or ""
    raw = raw.strip()
    if not raw:
        return {}

    try:
        import json

        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _coarse_platform_from_headers() -> str:
    # Prefer Client Hints if present.
    ch = (request.headers.get("Sec-CH-UA-Platform") or "").strip().strip('"')
    if ch:
        return ch[:32]

    ua = request.headers.get("User-Agent") or ""
    ua_l = ua.lower()
    if "windows" in ua_l:
        return "Windows"
    if "android" in ua_l:
        return "Android"
    if "iphone" in ua_l or "ipad" in ua_l or "ios" in ua_l:
        return "iOS"
    if "mac os x" in ua_l or "macintosh" in ua_l:
        return "macOS"
    if "linux" in ua_l:
        return "Linux"
    return "Other"


def _coarse_browser_from_headers() -> str:
    ua = request.headers.get("User-Agent") or ""
    ua_l = ua.lower()

    # Order matters.
    if "edg/" in ua_l:
        return "Edge"
    if "opr/" in ua_l or "opera" in ua_l:
        return "Opera"
    if "firefox/" in ua_l:
        return "Firefox"
    if "chrome/" in ua_l and "safari/" in ua_l and "edg/" not in ua_l:
        return "Chrome"
    if "safari/" in ua_l and "chrome/" not in ua_l:
        return "Safari"
    return "Other"


@app.route("/track/pageview", methods=["POST", "GET"])
def track_pageview():
    """Anonymous pageview counter.

    Stores only minimal, non-identifying data (no IP, no UA).
    Expected table: public.acfh_page_views (page TEXT, source TEXT, created_at default now()).
    """

    if request.method == "GET":
        page = (request.args.get("page") or "").strip()
        source = (request.args.get("source") or "").strip() or "web"
        client_id = (request.args.get("cid") or "").strip() or None
    else:
        data = _read_json_body_safely()
        page = str(data.get("page") or "").strip()
        source = str(data.get("source") or "").strip() or "web"
        client_id = str(data.get("cid") or "").strip() or None

    if not page or len(page) > 64:
        abort(400)

    if len(source) > 32:
        source = source[:32]

    if client_id is not None:
        # Client-generated random id (pseudonymous). Do not accept huge strings.
        if len(client_id) > 80:
            client_id = client_id[:80]

    client_platform = _coarse_platform_from_headers()
    client_browser = _coarse_browser_from_headers()

    payload = {
        "page": page,
        "source": source,
        "client_id": client_id,
        "client_platform": client_platform,
        "client_browser": client_browser,
    }

    debug = (request.args.get("debug") or "").strip() in {"1", "true", "yes"}

    if debug:
        ok, status, msg = _supabase_rest_insert_debug(table="acfh_page_views", payload=payload)
        return {
            "ok": ok,
            "status": status,
            "message": msg,
            "stored": {
                "page": page,
                "source": source,
                "client_platform": client_platform,
                "client_browser": client_browser,
                "client_id": bool(client_id),
            },
        }

    ok = _supabase_rest_insert(table="acfh_page_views", payload=payload)
    if not ok:
        # If Supabase isn't configured, don't break the UI.
        return ("", 204)

    return ("", 204)


@dataclass
class CacheItem:
    content: bytes
    content_type: str
    expires_at: float


_cache: dict[str, CacheItem] = {}


def _guess_content_type(path: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or fallback


def _supabase_object_public_url(path: str) -> str:
    # Keep slashes while encoding other characters.
    encoded = quote(path, safe="/")
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{encoded}"


def _fetch_from_supabase(path: str) -> Optional[Response]:
    if not SUPABASE_URL:
        return None

    url = _supabase_object_public_url(path)
    headers_with_auth: dict[str, str] = {}
    if SUPABASE_ANON_KEY:
        headers_with_auth["apikey"] = SUPABASE_ANON_KEY
        headers_with_auth["Authorization"] = f"Bearer {SUPABASE_ANON_KEY}"

    # Public buckets do not require auth headers. In some setups, sending an invalid/mismatched
    # JWT can cause a 400/401/403 and break local rendering. To be robust, retry without auth.
    upstream = requests.get(url, headers=headers_with_auth or None, timeout=30)
    if upstream.status_code in {400, 401, 403} and headers_with_auth:
        # Retry without auth headers (public buckets do not need JWT headers).
        upstream = requests.get(url, timeout=30)

    if upstream.status_code == 404:
        return None

    if upstream.status_code in {400, 401, 403}:
        # Treat auth-related errors as "not available" so callers can fall back to local.
        # This is especially important for /bootstrap.js.
        return None

    if upstream.status_code >= 400:
        abort(upstream.status_code)

    content = upstream.content
    content_type = upstream.headers.get("content-type") or _guess_content_type(path)

    return Response(content, status=200, content_type=content_type)


def _fetch_from_local(path: str) -> Optional[Response]:
    if not LOCAL_FALLBACK_DIR:
        return None

    # Prevent path traversal
    safe_path = path.replace("..", "")
    full_path = os.path.join(LOCAL_FALLBACK_DIR, safe_path)
    if not os.path.isfile(full_path):
        return None

    with open(full_path, "rb") as f:
        content = f.read()

    return Response(content, status=200, content_type=_guess_content_type(path))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/bootstrap.js")
def bootstrap_js():
    """Serve the bootstrap loader script.

    Priority:
      1) Supabase key: bootstrap.js
      2) Local file: flask_server/bootstrap.js
    """

    # Allow remote override from Supabase
    remote = _fetch_from_supabase("bootstrap.js")
    if remote is not None:
        remote.headers["cache-control"] = "no-store"
        return remote

    here = os.path.dirname(__file__)
    local_path = os.path.join(here, "bootstrap.js")
    with open(local_path, "rb") as f:
        content = f.read()

    return Response(content, status=200, content_type="application/javascript")


@app.get("/site/<path:asset_path>")
def site(asset_path: str):
    """Proxy any asset from Supabase Storage (or optional local fallback).

    The asset_path is used as-is as the object key in the bucket.
    Example: /site/options.html -> key "options.html".
    """

    # Simple cache
    now = time.time()
    cached = _cache.get(asset_path)
    if cached and cached.expires_at > now:
        resp = Response(cached.content, status=200, content_type=cached.content_type)
        resp.headers["cache-control"] = _cache_control_for_path(asset_path)
        return resp

    resp = _fetch_from_supabase(asset_path)
    if resp is None:
        resp = _fetch_from_local(asset_path)

    if resp is None:
        abort(404)

    # Materialize for caching (small/medium assets). If you store huge files, revisit.
    content_bytes = resp.get_data()
    content_type = resp.content_type or _guess_content_type(asset_path)

    _cache[asset_path] = CacheItem(
        content=content_bytes,
        content_type=content_type,
        expires_at=now + _ttl_seconds_for_path(asset_path),
    )

    out = Response(content_bytes, status=200, content_type=content_type)
    out.headers["cache-control"] = _cache_control_for_path(asset_path)
    return out


@app.get("/<path:asset_path>")
def site_root(asset_path: str):
        """Serve assets from Supabase also at the root path.

        This exists to support assets referenced with absolute paths like:
            - /images/logo.png
            - /lib/codemirror/codemirror.css

        It is especially important for CSS `url(/...)` which the JS loader does not rewrite.
        """

        # Avoid shadowing known endpoints (these are handled by dedicated routes).
        if asset_path in {"health", "bootstrap.js"} or asset_path.startswith("site/"):
                abort(404)

        return site(asset_path)


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, use_reloader=False)
