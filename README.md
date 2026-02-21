# client-proxy

`client-proxy` is a client wrapper for `uv`, `npm`, `pnpm`, and `cargo` that routes caches to an external SSD when mounted.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/gu1p/client-proxy/main/install.sh | bash
```

The installer is PEP-668 safe: it does not run `pip install` against system Python.
It also auto-adds `~/bin` to PATH (idempotently) in your zsh/bash startup file.
After install, run `exec $SHELL -l` (or open a new terminal), then `hash -r` if needed.

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
  - `cargo` -> `CARGO_TARGET_DIR` (`<SMART_SSD_BASE>/cargo-targets/<workspace-hash>-<toolchain-hash>`)
- Falls back to normal tool behavior when SSD is not mounted.
- `cargo` wrapping expects rustup proxies at `~/.cargo/bin/cargo` and `~/.cargo/bin/rustc`.

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
For cargo path layout, use `SMART_CARGO_SUBDIR` (defaults to `cargo-targets`).

## CI/CD policy

- CI enforces formatting, linting, static typing, and tests with coverage.
- Coverage is enforced at `>=95%`.
- CD re-runs all quality gates on every push to `main`.
- If all checks pass, CD auto-tags the next patch version (`vX.Y.Z`) and publishes a GitHub release.
