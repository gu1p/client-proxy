#!/usr/bin/env bash
set -euo pipefail

REPO="${CLIENT_PROXY_REPO:-gu1p/client-proxy}"
REF="${CLIENT_PROXY_REF:-main}"
BIN_DIR="${BIN_DIR:-$HOME/bin}"
WRAPPER_NAME="${WRAPPER_NAME:-client-proxy}"
WRAPPER_PATH="${BIN_DIR}/${WRAPPER_NAME}"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for installation." >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "${PYTHON_BIN} is required for installation." >&2
  exit 1
fi

mkdir -p "${BIN_DIR}"

curl -fsSL "${RAW_BASE}/main.py" -o "${WRAPPER_PATH}"
chmod +x "${WRAPPER_PATH}"

ln -sf "${WRAPPER_PATH}" "${BIN_DIR}/uv"
ln -sf "${WRAPPER_PATH}" "${BIN_DIR}/npm"
ln -sf "${WRAPPER_PATH}" "${BIN_DIR}/pnpm"

if [ ! -f "${BIN_DIR}/.env.example" ]; then
  curl -fsSL "${RAW_BASE}/.env.example" -o "${BIN_DIR}/.env.example"
fi

echo "Installed ${WRAPPER_NAME} and uv/npm/pnpm links into ${BIN_DIR}"

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *)
    echo "Add ${BIN_DIR} to PATH to activate the wrapper links."
    ;;
esac
