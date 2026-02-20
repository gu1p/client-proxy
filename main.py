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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

WRAPPER_NAMES = {"client-proxy", "client-proxy.py", "ssdwrap", "ssdwrap.py"}
TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


def parse_bool(value: object, *, default: bool, name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} must be a boolean-like value (accepted: 1/0,true/false,yes/no,y/n,on/off)"
    )


def normalize_mount(value: object) -> str:
    if value is None:
        return "/Volumes/SSD"
    mount = str(value).strip()
    return mount or "/Volumes/SSD"


def normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


@dataclass(frozen=True)
class Settings:
    smart_ssd_mount: str = "/Volumes/SSD"
    smart_ssd_base: str | None = None
    smart_uv_move_venv: bool = False
    smart_pnpm_import_method: str = ""
    smart_ssd_debug: bool = False

    @classmethod
    def from_runtime(cls, argv0: str, env: Mapping[str, str]) -> Settings:
        defaults = load_env_file_defaults(argv0, env)
        merged: dict[str, str] = {**defaults, **dict(env)}

        try:
            return cls(
                smart_ssd_mount=normalize_mount(merged.get("SMART_SSD_MOUNT")),
                smart_ssd_base=normalize_optional_text(merged.get("SMART_SSD_BASE")),
                smart_uv_move_venv=parse_bool(
                    merged.get("SMART_UV_MOVE_VENV"),
                    default=False,
                    name="SMART_UV_MOVE_VENV",
                ),
                smart_pnpm_import_method=normalize_optional_text(
                    merged.get("SMART_PNPM_IMPORT_METHOD")
                )
                or "",
                smart_ssd_debug=parse_bool(
                    merged.get("SMART_SSD_DEBUG"),
                    default=False,
                    name="SMART_SSD_DEBUG",
                ),
            )
        except ValueError as err:
            raise ValueError(f"invalid wrapper settings: {err}") from err

    @property
    def cache_base_dir(self) -> Path:
        if self.smart_ssd_base:
            return Path(self.smart_ssd_base)
        return Path(self.smart_ssd_mount) / "dev-caches"


def env_flag(name: str, default: bool = False, env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    value = source.get(name)
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def log(msg: str, settings: Settings | None = None, env: Mapping[str, str] | None = None) -> None:
    debug_enabled = (
        settings.smart_ssd_debug
        if settings is not None
        else env_flag("SMART_SSD_DEBUG", env=env, default=False)
    )
    if debug_enabled:
        print(f"[client-proxy] {msg}", file=sys.stderr)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
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


def wrapper_dirs_for_env_files(argv0: str, env: Mapping[str, str]) -> list[Path]:
    dirs: list[Path] = []

    invoked = Path(argv0).expanduser()
    invoked_path: Path | None = None
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


def env_files_for_wrapper(argv0: str, env: Mapping[str, str]) -> list[Path]:
    env_file = env.get("SMART_SSD_ENV_FILE", "").strip()
    if env_file:
        return [Path(env_file).expanduser()]

    files: list[Path] = []
    # Lower priority files first so invoked-wrapper directory wins.
    for wrapper_dir in reversed(wrapper_dirs_for_env_files(argv0, env)):
        files.extend([wrapper_dir / ".env.example", wrapper_dir / ".env"])
    return files


def load_env_file_defaults(argv0: str, env: Mapping[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in env_files_for_wrapper(argv0, env):
        if path.is_file():
            values.update(parse_env_file(path))
    return values


def is_mounted(mount_path: str) -> bool:
    path_obj = Path(mount_path)
    if not path_obj.is_dir():
        return False

    # Fast path (usually works on macOS/Linux)
    try:
        if os.path.ismount(mount_path):
            return True
    except OSError:
        pass

    # Fallback: parse `mount` output (macOS-friendly)
    try:
        out = subprocess.check_output(["mount"], text=True, stderr=subprocess.DEVNULL)
        return f" on {mount_path} " in out
    except (OSError, subprocess.CalledProcessError):
        return False


def sha1_12(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def find_project_root_for_uv(start_dir: Path) -> Path | None:
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


def find_real_executable(cmd_name: str, argv0: str, path_env: str | None = None) -> str | None:
    """
    Search PATH for cmd_name, skipping anything that resolves to this wrapper.
    This avoids recursion when PATH has ~/bin first.
    """
    me = wrapper_realpath(argv0)
    path = path_env if path_env is not None else os.environ.get("PATH", "")
    for path_dir in path.split(os.pathsep):
        if not path_dir:
            path_dir = "."
        candidate = os.path.join(path_dir, cmd_name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            if os.path.realpath(candidate) == me:
                continue
            return candidate
    return None


def determine_tool_and_args(argv: Sequence[str]) -> tuple[str, list[str]]:
    """
    If invoked as uv/npm/pnpm (via symlink), tool is basename(argv[0]).
    If invoked as client-proxy, allow: client-proxy uv <args...>
    """
    if not argv:
        raise ValueError("Usage: client-proxy <uv|npm|pnpm> [args...]")

    invoked = Path(argv[0]).name
    if invoked in WRAPPER_NAMES:
        if len(argv) < 2:
            raise ValueError("Usage: client-proxy <uv|npm|pnpm> [args...]")
        return argv[1], list(argv[2:])
    return invoked, list(argv[1:])


class UvCliHandler:
    @staticmethod
    def configure(env: dict[str, str], base_dir: Path, settings: Settings | None = None) -> None:
        uv_cache = base_dir / "uv-cache"
        uv_cache.mkdir(parents=True, exist_ok=True)
        env["UV_CACHE_DIR"] = str(uv_cache)
        log(f"UV_CACHE_DIR={env['UV_CACHE_DIR']}", settings=settings, env=env)

        should_move_venv = (
            settings.smart_uv_move_venv
            if settings
            else env_flag("SMART_UV_MOVE_VENV", env=env, default=False)
        )
        if should_move_venv:
            root = find_project_root_for_uv(Path.cwd())
            if root:
                root_id = sha1_12(str(root.resolve()))
                venv_dir = base_dir / "uv-venvs" / root_id
                venv_dir.mkdir(parents=True, exist_ok=True)
                env["UV_PROJECT_ENVIRONMENT"] = str(venv_dir)
                log(
                    f"UV_PROJECT_ENVIRONMENT={env['UV_PROJECT_ENVIRONMENT']}",
                    settings=settings,
                    env=env,
                )


class NpmCliHandler:
    @staticmethod
    def configure(env: dict[str, str], base_dir: Path, settings: Settings | None = None) -> None:
        npm_cache = base_dir / "npm-cache"
        npm_cache.mkdir(parents=True, exist_ok=True)
        env["npm_config_cache"] = str(npm_cache)
        log(f"npm_config_cache={env['npm_config_cache']}", settings=settings, env=env)


class PnpmCliHandler:
    @staticmethod
    def configure(env: dict[str, str], base_dir: Path, settings: Settings | None = None) -> None:
        pnpm_store = base_dir / "pnpm-store"
        pnpm_store.mkdir(parents=True, exist_ok=True)
        env["npm_config_store_dir"] = str(pnpm_store)
        log(f"npm_config_store_dir={env['npm_config_store_dir']}", settings=settings, env=env)

        method = (
            settings.smart_pnpm_import_method
            if settings
            else env.get("SMART_PNPM_IMPORT_METHOD", "").strip()
        )
        if method:
            # pnpm reads npm-style config env vars too
            env["npm_config_package_import_method"] = method
            log(
                f"npm_config_package_import_method={env['npm_config_package_import_method']}",
                settings=settings,
                env=env,
            )


CliEnvHandler = Callable[[dict[str, str], Path, Settings], None]
CLI_ENV_HANDLERS: dict[str, CliEnvHandler] = {
    "uv": UvCliHandler.configure,
    "npm": NpmCliHandler.configure,
    "pnpm": PnpmCliHandler.configure,
}


def apply_env_for_tool(tool: str, env: dict[str, str], settings: Settings) -> None:
    base_dir = settings.cache_base_dir
    base_dir.mkdir(parents=True, exist_ok=True)

    handler = CLI_ENV_HANDLERS.get(tool)
    if handler is None:
        # Unknown tool: do nothing special
        log(
            f"Tool '{tool}' not configured; running without SSD cache redirect.",
            settings=settings,
            env=env,
        )
        return

    handler(env, base_dir, settings)


def main(argv: Sequence[str] | None = None) -> int:
    argsv = list(argv) if argv is not None else list(sys.argv)
    try:
        tool, args = determine_tool_and_args(argsv)
    except ValueError as err:
        print(err, file=sys.stderr)
        return 2

    env = dict(os.environ)
    try:
        settings = Settings.from_runtime(argsv[0], env)
    except ValueError as err:
        print(f"client-proxy: {err}", file=sys.stderr)
        return 2

    if is_mounted(settings.smart_ssd_mount) and tool in CLI_ENV_HANDLERS:
        log(f"SSD mounted at {settings.smart_ssd_mount}", settings=settings, env=env)
        apply_env_for_tool(tool, env, settings)
    else:
        log(
            f"SSD not mounted or tool unsupported (tool={tool})",
            settings=settings,
            env=env,
        )

    real = find_real_executable(tool, argsv[0], path_env=env.get("PATH"))
    if not real:
        print(
            f"client-proxy: can't find real '{tool}' in PATH (after skipping wrapper).",
            file=sys.stderr,
        )
        print("Fix: ensure the real tool is installed and reachable in PATH.", file=sys.stderr)
        return 127

    log(f"exec: {real} {' '.join(args)}", settings=settings, env=env)
    os.execvpe(real, [real, *args], env)
    return 0


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()
