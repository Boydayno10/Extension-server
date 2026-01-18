import argparse
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default or ""
    return value


def _put_object(
    *,
    supabase_url: str,
    bucket: str,
    object_key: str,
    file_path: Path,
    token: str,
    upsert: bool,
    timeout_s: int,
):
    # Some Windows setups map .html to text/plain via registry.
    # Force common web types for better behavior when files are accessed directly.
    suffix = file_path.suffix.lower()
    if suffix in {".html", ".htm"}:
        content_type = "text/html; charset=utf-8"
    elif suffix == ".css":
        content_type = "text/css; charset=utf-8"
    elif suffix == ".js":
        content_type = "application/javascript; charset=utf-8"
    elif suffix == ".json":
        content_type = "application/json; charset=utf-8"
    elif suffix == ".svg":
        content_type = "image/svg+xml"
    else:
        content_type, _ = mimetypes.guess_type(str(file_path))
        if not content_type:
            content_type = "application/octet-stream"

    encoded_key = quote(object_key.replace("\\", "/"), safe="/")
    url = f"{supabase_url}/storage/v1/object/{bucket}/{encoded_key}"
    if upsert:
        url += "?upsert=true"

    headers = {
        "apikey": token,
        "Authorization": f"Bearer {token}",
        "content-type": content_type,
    }

    with open(file_path, "rb") as f:
        resp = requests.put(url, headers=headers, data=f, timeout=timeout_s)

    if resp.status_code >= 400:
        raise RuntimeError(f"Upload failed: {object_key} ({resp.status_code}) {resp.text[:300]}")


def main():
    # Prefer loading `flask_server/.env` regardless of current working directory.
    here = Path(__file__).resolve().parent
    load_dotenv(dotenv_path=here / ".env")
    # Fallback to default discovery (useful if user keeps .env at repo root).
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Upload the local supabase_seed/ folder into Supabase Storage."
    )
    parser.add_argument("--seed-dir", default=_env("SUPABASE_SEED_DIR", ""), help="Path to supabase_seed")
    parser.add_argument("--bucket", default=_env("SUPABASE_BUCKET", "web"), help="Supabase bucket name")
    parser.add_argument(
        "--token-env",
        default="SUPABASE_SERVICE_ROLE_KEY",
        help="Env var name containing a key allowed to upload objects",
    )
    parser.add_argument(
        "--preserve-web-prefix",
        action="store_true",
        help="Preserve legacy object keys under web/** instead of flattening supabase_seed/web/** to bucket root",
    )
    parser.add_argument("--upsert", action="store_true", help="Overwrite existing objects")
    parser.add_argument("--timeout", type=int, default=60, help="Request timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="List what would be uploaded")
    args = parser.parse_args()

    supabase_url = _env("SUPABASE_URL").rstrip("/")
    if not supabase_url:
        raise SystemExit("Missing SUPABASE_URL in .env")

    token = _env(args.token_env)
    if not token:
        raise SystemExit(
            f"Missing upload token. Set {args.token_env} in .env (recommended: service role key)."
        )

    seed_dir = args.seed_dir
    if not seed_dir:
        seed_dir = str(Path(__file__).resolve().parents[1] / "supabase_seed")

    seed_path = Path(seed_dir).resolve()
    if not seed_path.is_dir():
        raise SystemExit(f"Seed dir not found: {seed_path}")

    def _walk_files(base: Path):
        for root, _, filenames in os.walk(base):
            for name in filenames:
                yield Path(root) / name

    # If supabase_seed/web exists, default to uploading its contents to bucket root
    # (so web/index.html becomes index.html), while still uploading root files like
    # runtime-config.json.
    web_root = seed_path / "web"
    flatten_web = web_root.is_dir() and not args.preserve_web_prefix

    files: list[tuple[str, Path]] = []
    if flatten_web:
        web_files: list[tuple[str, Path]] = []
        for file_path in _walk_files(web_root):
            rel = file_path.relative_to(web_root)
            object_key = rel.as_posix()
            web_files.append((object_key, file_path))

        web_keys = {k for k, _ in web_files}

        root_files: list[tuple[str, Path]] = []
        for file_path in _walk_files(seed_path):
            # Skip the web subtree; it is handled above.
            if web_root in file_path.parents:
                continue
            rel = file_path.relative_to(seed_path)
            object_key = rel.as_posix()

            # Avoid duplicates if the same key exists under supabase_seed/web.
            if object_key in web_keys:
                continue

            root_files.append((object_key, file_path))

        files = root_files + web_files
    else:
        for file_path in _walk_files(seed_path):
            rel = file_path.relative_to(seed_path)
            object_key = rel.as_posix()
            files.append((object_key, file_path))

    files.sort(key=lambda t: t[0])

    if not files:
        raise SystemExit(f"No files found under: {seed_path}")

    print(f"Seed:   {seed_path}")
    print(f"Bucket: {args.bucket}")
    print(f"Files:  {len(files)}")
    if flatten_web:
        print("Mode:   flatten supabase_seed/web/** -> bucket root")
    else:
        print("Mode:   preserve paths")
    print(f"Upsert: {args.upsert}")
    print(f"DryRun: {args.dry_run}")

    if args.dry_run:
        for object_key, _ in files[:50]:
            print(f"- {object_key}")
        if len(files) > 50:
            print(f"... (+{len(files) - 50} more)")
        return

    for i, (object_key, file_path) in enumerate(files, start=1):
        _put_object(
            supabase_url=supabase_url,
            bucket=args.bucket,
            object_key=object_key,
            file_path=file_path,
            token=token,
            upsert=args.upsert,
            timeout_s=args.timeout,
        )
        if i == 1 or i % 25 == 0 or i == len(files):
            print(f"Uploaded {i}/{len(files)}")

    print("Done.")


if __name__ == "__main__":
    main()
