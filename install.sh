#!/usr/bin/env bash
set -euo pipefail

REPO="${CLIENT_PROXY_REPO:-gu1p/client-proxy}"
REF="${CLIENT_PROXY_REF:-main}"
BIN_DIR="${BIN_DIR:-$HOME/bin}"
WRAPPER_NAME="${WRAPPER_NAME:-client-proxy}"
WRAPPER_PATH="${BIN_DIR}/${WRAPPER_NAME}"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
PATH_BLOCK_BEGIN="# >>> client-proxy PATH >>>"
PATH_BLOCK_END="# <<< client-proxy PATH <<<"
PATH_RC_UPDATED=0
PATH_RC_FILE=""

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required for installation." >&2
  exit 1
fi

resolve_path_value() {
  if [ "${BIN_DIR}" = "${HOME}/bin" ]; then
    printf '%s' "\$HOME/bin"
  else
    printf '%s' "${BIN_DIR}"
  fi
}

detect_rc_file() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"

  case "${shell_name}" in
    zsh)
      printf '%s\n' "${HOME}/.zshrc"
      return
      ;;
    bash)
      if [ -f "${HOME}/.bashrc" ]; then
        printf '%s\n' "${HOME}/.bashrc"
      elif [ -f "${HOME}/.bash_profile" ]; then
        printf '%s\n' "${HOME}/.bash_profile"
      elif [ "$(uname -s)" = "Darwin" ]; then
        printf '%s\n' "${HOME}/.bash_profile"
      else
        printf '%s\n' "${HOME}/.bashrc"
      fi
      return
      ;;
  esac

  for candidate in "${HOME}/.zshrc" "${HOME}/.bashrc" "${HOME}/.bash_profile"; do
    if [ -f "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done

  printf '%s\n' "${HOME}/.zshrc"
}

ensure_path_in_shell_rc() {
  local rc_file path_value path_line
  rc_file="$(detect_rc_file)"
  path_value="$(resolve_path_value)"
  path_line="export PATH=\"${path_value}:\$PATH\""

  PATH_RC_FILE="${rc_file}"

  if [ -f "${rc_file}" ] && grep -Fq "${PATH_BLOCK_BEGIN}" "${rc_file}"; then
    PATH_RC_UPDATED=0
    return
  fi

  if [ -f "${rc_file}" ] && grep -Fq "${path_line}" "${rc_file}"; then
    PATH_RC_UPDATED=0
    return
  fi

  if ! touch "${rc_file}" 2>/dev/null; then
    PATH_RC_UPDATED=-1
    echo "Could not write ${rc_file} to update PATH automatically." >&2
    echo "Add this line to your shell startup file:" >&2
    echo "${path_line}" >&2
    return
  fi

  if cat >> "${rc_file}" <<EOF

${PATH_BLOCK_BEGIN}
# Added by client-proxy installer
${path_line}
${PATH_BLOCK_END}
EOF
  then
    PATH_RC_UPDATED=1
  else
    PATH_RC_UPDATED=-1
    echo "Failed to append PATH configuration to ${rc_file}." >&2
    echo "Add this line to your shell startup file:" >&2
    echo "${path_line}" >&2
  fi
}

mkdir -p "${BIN_DIR}"

curl -fsSL "${RAW_BASE}/main.py" -o "${WRAPPER_PATH}"
chmod +x "${WRAPPER_PATH}"

ln -sf "${WRAPPER_PATH}" "${BIN_DIR}/uv"
ln -sf "${WRAPPER_PATH}" "${BIN_DIR}/npm"
ln -sf "${WRAPPER_PATH}" "${BIN_DIR}/pnpm"

if [ ! -f "${BIN_DIR}/.env.example" ]; then
  curl -fsSL "${RAW_BASE}/.env.example" -o "${BIN_DIR}/.env.example"
fi

ensure_path_in_shell_rc

echo "Installed ${WRAPPER_NAME} and uv/npm/pnpm links into ${BIN_DIR}"

if [ "${PATH_RC_UPDATED}" = "1" ]; then
  echo "Updated PATH in ${PATH_RC_FILE}"
elif [ "${PATH_RC_UPDATED}" = "0" ]; then
  echo "PATH already configured in ${PATH_RC_FILE}"
else
  echo "PATH was not updated automatically. Use the manual instructions above."
fi

echo "Open a new terminal or run: exec \$SHELL -l"
echo "If this shell still resolves old binaries, run: hash -r"
