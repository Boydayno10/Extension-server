# Flask Supabase Content Proxy (Local)

This folder implements the **local Flask server** used by the web "shell" pages.

## What it does

- Exposes `GET /site/<path>` to serve any file stored in **Supabase Storage**.
- Exposes `GET /bootstrap.js` used by the local `web/index.html` and `web/options.html` shells.
- Exposes `POST /track/pageview` to record anonymous page view counts in Supabase (no IP/UA stored).
- Adds permissive CORS so your local web server (Live Server, etc.) can fetch from Flask.

## Env vars

Copy `.env.example` to `.env` and fill:

- `SUPABASE_URL`
- `SUPABASE_BUCKET`
- `SUPABASE_ANON_KEY` (recommended)

Optional:

- `LOCAL_FALLBACK_DIR` (serves from disk if the object is missing on Supabase)
- `HTML_CACHE_TTL_SECONDS` (default 30) – how long HTML stays in the Flask in-memory cache + browser cache
- `STATIC_CACHE_TTL_SECONDS` (default 86400) – cache CSS/JS/images aggressively so refreshes are consistently fast
- `SUPABASE_SERVICE_ROLE_KEY` (only for local upload scripts; never expose to the browser)

## Anonymous pageview tracking (options page)

This repo includes a minimal, privacy-safe counter for the Options page.

- Client sends (anonymous): `page`, `source` and a random `client_id` stored locally (pseudonymous)
- Server stores: `page`, `source`, `client_id`, `client_platform`, `client_browser`, `created_at`

Privacy notes:
- No IP is stored.
- No full User-Agent is stored.
- `client_id` is a random UUID generated in the browser and saved in localStorage. It is used only to estimate *unique clients per day*.

### 1) Create the table

Run the SQL from:

- `flask_server/supabase_pageviews.sql`

This SQL also creates views for monitoring:

- `public.acfh_page_views_daily_summary` (recommended): daily accesses + daily unique clients by `page` and `source`
- `public.acfh_page_views_daily`: same, but also broken down by browser/platform

### 2) Ensure the server can write

Set `SUPABASE_SERVICE_ROLE_KEY` in `flask_server/.env`.

### 3) Verify

With Flask running, open the options page and check in Supabase:

- Table: `public.acfh_page_views`

### Local-first (sem Supabase)

Você pode validar todo o fluxo **sem subir nada no Supabase**:

- deixe `SUPABASE_URL` vazio
- configure `LOCAL_FALLBACK_DIR` para `...\\supabase_seed`

Assim o Flask vai servir os arquivos a partir do disco, mas com a **mesma estrutura de paths** que você vai usar no Supabase.

## Object keys (layout recomendado)

Para URLs bonitas no deploy (ex: `https://.../` e `https://.../options.html`), suba os arquivos do site **sem** o prefixo `web/`.

- `supabase_seed/web/**`  -> object keys `/**` (ex: `index.html`, `options.html`, `style.css`, `lib/...`, `images/...`)
- `supabase_seed/runtime-config.json` -> object key `runtime-config.json`

O script [flask_server/upload_seed.py](flask_server/upload_seed.py) já faz isso automaticamente quando encontra `supabase_seed/web/`.

**Backward-compat:** se você ainda abrir `.../web/index.html` localmente, o loader agora remove o prefixo `web/` ao buscar no Supabase.

## Run

Install deps:

- `pip install -r flask_server/requirements.txt`

Start Flask:

- `python flask_server/app.py`

Default: `http://127.0.0.1:5000`

## Deploy on Render

This repo includes a ready-to-use Render blueprint at:

- `render.yaml` (repo root)

### Steps

1) Push your repo to GitHub.
2) In Render: **New +** → **Blueprint** → select your repo.
3) Render will create a web service from `render.yaml`.

### Required env vars (Render)

Set these in the Render service dashboard:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY` (recommended; works with public buckets too)
- `SUPABASE_SERVICE_ROLE_KEY` (required only if you want `/track/pageview` to insert into Postgres)

Optional:

- `SUPABASE_BUCKET` (default `web`)
- `HTML_CACHE_TTL_SECONDS` (default `30`)
- `STATIC_CACHE_TTL_SECONDS` (default `86400`)
- `LOCAL_FALLBACK_DIR` (leave empty on Render)

### What Render will run

The blueprint uses Gunicorn:

- `gunicorn app:app --bind 0.0.0.0:$PORT`

## Supabase setup

### 1) Upload objects

Upload the contents of the local folder `supabase_seed/` to the **root** of the `web` bucket.

Recommended layout (no `web/` prefix):

- `supabase_seed/web/**`  -> object keys `/**` (ex: `index.html`, `options.html`, `style.css`, `lib/...`, `images/...`)
- `supabase_seed/runtime-config.json` -> object key `runtime-config.json`

#### Option A: Upload via dashboard (manual)

- Storage -> Buckets -> `web` -> upload folders/files so the bucket ends up containing `index.html`, `options.html`, `style.css`, `lib/`, `images/`, and `runtime-config.json`.

#### Option B: Upload via script (recommended)

1) Create `flask_server/.env` by copying `.env.example`
2) Set:
  - `SUPABASE_URL=...`
  - `SUPABASE_BUCKET=web`
  - `SUPABASE_SERVICE_ROLE_KEY=...` (only used locally for uploading)
3) Run:
  - `python flask_server/upload_seed.py --upsert`

Legacy behavior (keep `web/**` object keys):

- `python flask_server/upload_seed.py --upsert --preserve-web-prefix`

### 2) Quick URL checks

Open these in a browser (should NOT be 404):

- `https://<ref>.supabase.co/storage/v1/object/public/web/options.html`
- `https://<ref>.supabase.co/storage/v1/object/public/web/lib/codemirror/codemirror.js`
- `https://<ref>.supabase.co/storage/v1/object/public/web/images/logo-128.png`

## How the web shells map to Supabase keys

The loader uses the browser path as the Storage object key, with a small compatibility rule:

- If the path starts with `web/` (ex: `/web/options.html`), it strips that prefix when requesting from Supabase.

Example:

- If you open `http://127.0.0.1:5500/web/options.html`
- The loader requests `http://127.0.0.1:5000/site/options.html`
- So Supabase must contain the object key `options.html` (and any referenced assets).

A full backup of the current website content was copied to:

- `supabase_seed/web/`

Upload that folder into your Supabase bucket **without** the `web/` prefix (so it becomes `index.html`, `options.css`, etc.).

Se você também usa paths absolutos no HTML/CSS (ex.: `/images/...` e `/lib/...`), isso já está incluído dentro de `supabase_seed/web/`.

## Configuring the Flask origin from the browser

By default the shells use `http://127.0.0.1:5000`.

To override without code changes:

- `localStorage.setItem('ACFH_FLASK_ORIGIN', 'http://127.0.0.1:5000')`

## Ads runtime control

The loader delays the Google Ads script until after the page is rendered.

It also tries to read a JSON file from Supabase:

- `runtime-config.json`

Example:

```json
{
  "adsense": { "enabled": true }
}
```

If `enabled` is `false`, the loader will skip injecting the Ads script.
# Extension-server
