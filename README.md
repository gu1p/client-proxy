# client-proxy

`client-proxy` is a client wrapper for `uv`, `npm`, and `pnpm` that routes caches to an external SSD when mounted.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/gu1p/client-proxy/main/install.sh | bash
```

## Development setup

```bash
make setup
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

## Quality gates

```bash
make format-check
make lint
make typecheck
make test
make check
```

## Configuration

Defaults are loaded from `.env.example` and `.env` in the wrapper directory. Existing shell env vars always win.

## CI/CD policy

- CI enforces formatting, linting, static typing, and tests with coverage.
- Coverage is enforced at `>=95%`.
- CD re-runs all quality gates on every push to `main`.
- If all checks pass, CD auto-tags the next patch version (`vX.Y.Z`) and publishes a GitHub release.
