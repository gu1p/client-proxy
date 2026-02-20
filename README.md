# client-proxy

`client-proxy` is a client wrapper for `uv`, `npm`, and `pnpm` that routes caches to an external SSD when mounted.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/gu1p/client-proxy/main/install.sh | bash
```

## What it does

- Detects whether your SSD mount is available (`SMART_SSD_MOUNT`, default `/Volumes/SSD`).
- Redirects caches when mounted:
  - `uv` -> `UV_CACHE_DIR` (and optional `UV_PROJECT_ENVIRONMENT`)
  - `npm` -> `npm_config_cache`
  - `pnpm` -> `npm_config_store_dir` (and optional `npm_config_package_import_method`)
- Falls back to normal tool behavior when SSD is not mounted.

## Local install (from source)

```bash
make install
```

## Run tests

```bash
make test
```

## Configuration

Defaults are loaded from `.env.example` and `.env` in the wrapper directory. Existing shell env vars always win.
