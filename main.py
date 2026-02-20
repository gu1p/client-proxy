#!/usr/bin/env python3
"""
client-proxy: a tiny wrapper for uv/npm/pnpm that redirects caches to an external SSD
when it's mounted.

How it works:
- Detects the command name it was invoked as (uv, npm, pnpm)
  OR you can call it as: client-proxy uv <args...>
- Loads optional defaults from `.env.example` and `.env` next to the wrapper binary.
- If /Volumes/SSD is mounted, sets tool-specific env vars:
    uv   -> UV_CACHE_DIR, optionally UV_PROJECT_ENVIRONMENT
    npm  -> npm_config_cache
    pnpm -> npm_config_store_dir, optionally npm_config_package_import_method
- Execs the *real* tool from PATH (skipping this wrapper)
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple


WRAPPER_NAMES = {"client-proxy", "client-proxy.py", "ssdwrap", "ssdwrap.py"}


def env_flag(name: str, default: bool = False, env: Optional[Mapping[str, str]] = None) -> bool:
    source = env if env is not None else os.environ
    v = source.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def log(msg: str, env: Optional[Mapping[str, str]] = None) -> None:
    if env_flag("SMART_SSD_DEBUG", env=env, default=False):
        print(f"[client-proxy] {msg}", file=sys.stderr)


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    return values


def wrapper_dirs_for_env_files(argv0: str, env: Mapping[str, str]) -> List[Path]:
    dirs: List[Path] = []

    invoked = Path(argv0).expanduser()
    invoked_path: Optional[Path] = None
    if invoked.is_absolute() or "/" in argv0:
        invoked_path = invoked
    else:
        for path_dir in env.get("PATH", "").split(os.pathsep):
            path_dir = path_dir or "."
            candidate = Path(path_dir) / argv0
            if candidate.is_file() and os.access(candidate, os.X_OK):
                invoked_path = candidate
                break

    if invoked_path is not None:
        dirs.append(Path(os.path.abspath(invoked_path)).parent)

    real_dir = Path(wrapper_realpath(argv0)).parent
    if real_dir not in dirs:
        dirs.append(real_dir)
    return dirs


def env_files_for_wrapper(argv0: str, env: Mapping[str, str]) -> List[Path]:
    env_file = env.get("SMART_SSD_ENV_FILE", "").strip()
    if env_file:
        return [Path(env_file).expanduser()]

    files: List[Path] = []
    # Lower priority files first so invoked-wrapper directory wins.
    for wrapper_dir in reversed(wrapper_dirs_for_env_files(argv0, env)):
        files.extend([wrapper_dir / ".env.example", wrapper_dir / ".env"])
    return files


def load_env_file_defaults(argv0: str, env: Mapping[str, str]) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for path in env_files_for_wrapper(argv0, env):
        if path.is_file():
            values.update(parse_env_file(path))
    return values


def is_mounted(mount_path: str) -> bool:
    p = Path(mount_path)
    if not p.is_dir():
        return False

    # Fast path (usually works on macOS/Linux)
    try:
        if os.path.ismount(mount_path):
            return True
    except Exception:
        pass

    # Fallback: parse `mount` output (macOS-friendly)
    try:
        out = subprocess.check_output(["mount"], text=True, stderr=subprocess.DEVNULL)
        return f" on {mount_path} " in out
    except Exception:
        return False


def sha1_12(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def find_project_root_for_uv(start_dir: Path) -> Optional[Path]:
    """
    Walk up from cwd looking for pyproject.toml or uv.toml.
    Used only if SMART_UV_MOVE_VENV=1 to move the project's venv off-repo.
    """
    cur = start_dir.resolve()
    while True:
        if (cur / "pyproject.toml").is_file() or (cur / "uv.toml").is_file():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def wrapper_realpath(argv0: str) -> str:
    # argv0 may be a symlink name like ~/bin/uv -> ~/bin/client-proxy
    return os.path.realpath(argv0)


def find_real_executable(cmd_name: str, argv0: str) -> Optional[str]:
    """
    Search PATH for cmd_name, skipping anything that resolves to this wrapper.
    This avoids recursion when PATH has ~/bin first.
    """
    me = wrapper_realpath(argv0)
    path = os.environ.get("PATH", "")
    for d in path.split(os.pathsep):
        if not d:
            d = "."
        cand = os.path.join(d, cmd_name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            if os.path.realpath(cand) == me:
                continue
            return cand
    return None


def determine_tool_and_args(argv: List[str]) -> Tuple[str, List[str]]:
    """
    If invoked as uv/npm/pnpm (via symlink), tool is basename(argv[0]).
    If invoked as client-proxy, allow: client-proxy uv <args...>
    """
    invoked = Path(argv[0]).name
    if invoked in WRAPPER_NAMES:
        if len(argv) < 2:
            raise ValueError("Usage: client-proxy <uv|npm|pnpm> [args...]")
        return argv[1], argv[2:]
    return invoked, argv[1:]


class UvCliHandler:
    @staticmethod
    def configure(env: Dict[str, str], base_dir: Path) -> None:
        uv_cache = base_dir / "uv-cache"
        uv_cache.mkdir(parents=True, exist_ok=True)
        env["UV_CACHE_DIR"] = str(uv_cache)
        log(f"UV_CACHE_DIR={env['UV_CACHE_DIR']}", env=env)

        if env_flag("SMART_UV_MOVE_VENV", env=env, default=False):
            root = find_project_root_for_uv(Path.cwd())
            if root:
                rid = sha1_12(str(root.resolve()))
                venv_dir = base_dir / "uv-venvs" / rid
                venv_dir.mkdir(parents=True, exist_ok=True)
                env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
                log(f"UV_PROJECT_ENVIRONMENT={env['UV_PROJECT_ENVIRONMENT']}", env=env)


class NpmCliHandler:
    @staticmethod
    def configure(env: Dict[str, str], base_dir: Path) -> None:
        npm_cache = base_dir / "npm-cache"
        npm_cache.mkdir(parents=True, exist_ok=True)
        env["npm_config_cache"] = str(npm_cache)
        log(f"npm_config_cache={env['npm_config_cache']}", env=env)


class PnpmCliHandler:
    @staticmethod
    def configure(env: Dict[str, str], base_dir: Path) -> None:
        pnpm_store = base_dir / "pnpm-store"
        pnpm_store.mkdir(parents=True, exist_ok=True)
        env["npm_config_store_dir"] = str(pnpm_store)
        log(f"npm_config_store_dir={env['npm_config_store_dir']}", env=env)

        method = env.get("SMART_PNPM_IMPORT_METHOD", "").strip()
        if method:
            # pnpm reads npm-style config env vars too
            env["npm_config_package_import_method"] = method
            log(f"npm_config_package_import_method={env['npm_config_package_import_method']}", env=env)


CLI_ENV_HANDLERS: Dict[str, Callable[[Dict[str, str], Path], None]] = {
    "uv": UvCliHandler.configure,
    "npm": NpmCliHandler.configure,
    "pnpm": PnpmCliHandler.configure,
}


def apply_env_for_tool(tool: str, env: Dict[str, str], ssd_mount: str) -> None:
    base = env.get("SMART_SSD_BASE", os.path.join(ssd_mount, "dev-caches"))
    base_dir = Path(base)
    base_dir.mkdir(parents=True, exist_ok=True)

    handler = CLI_ENV_HANDLERS.get(tool)
    if handler is None:
        # Unknown tool: do nothing special
        log(f"Tool '{tool}' not configured; running without SSD cache redirect.", env=env)
        return

    handler(env, base_dir)


def main(argv: Optional[List[str]] = None) -> int:
    argsv = argv if argv is not None else sys.argv
    try:
        tool, args = determine_tool_and_args(argsv)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 2

    env = dict(os.environ)
    env_file_defaults = load_env_file_defaults(argsv[0], env)
    for key, value in env_file_defaults.items():
        env.setdefault(key, value)

    ssd_mount = env.get("SMART_SSD_MOUNT", "/Volumes/SSD")

    if is_mounted(ssd_mount) and tool in CLI_ENV_HANDLERS:
        log(f"SSD mounted at {ssd_mount}", env=env)
        apply_env_for_tool(tool, env, ssd_mount)
    else:
        log(f"SSD not mounted or tool unsupported (tool={tool})", env=env)

    real = find_real_executable(tool, argsv[0])
    if not real:
        print(f"client-proxy: can't find real '{tool}' in PATH (after skipping wrapper).", file=sys.stderr)
        print("Fix: ensure the real tool is installed and reachable in PATH.", file=sys.stderr)
        return 127

    log(f"exec: {real} {' '.join(args)}", env=env)
    os.execvpe(real, [real, *args], env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
