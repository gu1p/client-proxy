from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import main as client_proxy

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_SCRIPT = REPO_ROOT / "main.py"
ENV_KEYS = [
    "UV_CACHE_DIR",
    "UV_PROJECT_ENVIRONMENT",
    "npm_config_cache",
    "npm_config_store_dir",
    "npm_config_package_import_method",
    "CARGO_TARGET_DIR",
]

FAKE_TOOL = """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

keys = {keys!r}
payload = {{
    "argv": sys.argv,
    "env": {{key: os.environ.get(key) for key in keys}},
}}
print(json.dumps(payload))
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepare_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    real_bin = tmp_path / "real-bin"
    cache_root = tmp_path / "cache-root"
    bin_dir.mkdir()
    real_bin.mkdir()
    cache_root.mkdir()

    wrapper = bin_dir / "client-proxy"
    shutil.copy2(MAIN_SCRIPT, wrapper)
    wrapper.chmod(0o755)
    for tool in ("uv", "npm", "pnpm", "cargo"):
        (bin_dir / tool).symlink_to(wrapper)
        _write_executable(real_bin / tool, FAKE_TOOL.format(keys=ENV_KEYS))

    return bin_dir, real_bin, cache_root


def _prepare_rustup_proxies(home_dir: Path, manifest: Path, rustc_vv: str) -> tuple[Path, Path]:
    rustup_bin = home_dir / ".cargo" / "bin"
    rustup_bin.mkdir(parents=True, exist_ok=True)

    cargo_proxy = rustup_bin / "cargo"
    rustc_proxy = rustup_bin / "rustc"

    cargo_proxy.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from __future__ import annotations",
                "import json",
                "import os",
                "import sys",
                f"manifest = {str(manifest)!r}",
                "keys = " + repr(ENV_KEYS),
                "if 'locate-project' in sys.argv:",
                "    print(manifest)",
                "    raise SystemExit(0)",
                "payload = {",
                "    'argv': sys.argv,",
                "    'env': {k: os.environ.get(k) for k in keys},",
                "}",
                "print(json.dumps(payload))",
            ]
        ),
        encoding="utf-8",
    )
    cargo_proxy.chmod(0o755)

    rustc_proxy.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "from __future__ import annotations",
                f"print({rustc_vv!r})",
            ]
        ),
        encoding="utf-8",
    )
    rustc_proxy.chmod(0o755)
    return cargo_proxy, rustc_proxy


def _run_and_parse(command: list[str], env: dict[str, str], cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(command, env=env, cwd=cwd, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


@pytest.mark.e2e
def test_wrapper_invocation_sets_npm_cache_when_mounted(tmp_path: Path) -> None:
    bin_dir, real_bin, cache_root = _prepare_tree(tmp_path)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = "/"
    env["SMART_SSD_BASE"] = str(cache_root)

    payload = _run_and_parse(
        [str(bin_dir / "client-proxy"), "npm", "install"], env=env, cwd=tmp_path
    )

    assert payload["argv"][1:] == ["install"]
    assert payload["env"]["npm_config_cache"] == str(cache_root / "npm-cache")


@pytest.mark.e2e
def test_symlink_invocation_sets_pnpm_store_and_import_method(tmp_path: Path) -> None:
    bin_dir, real_bin, cache_root = _prepare_tree(tmp_path)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = "/"
    env["SMART_SSD_BASE"] = str(cache_root)
    env["SMART_PNPM_IMPORT_METHOD"] = "hardlink"

    payload = _run_and_parse([str(bin_dir / "pnpm"), "add", "left-pad"], env=env, cwd=tmp_path)

    assert payload["argv"][1:] == ["add", "left-pad"]
    assert payload["env"]["npm_config_store_dir"] == str(cache_root / "pnpm-store")
    assert payload["env"]["npm_config_package_import_method"] == "hardlink"


@pytest.mark.e2e
def test_uv_invocation_sets_uv_cache_and_project_environment(tmp_path: Path) -> None:
    bin_dir, real_bin, cache_root = _prepare_tree(tmp_path)
    project_root = tmp_path / "project"
    nested = project_root / "src"
    nested.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = "/"
    env["SMART_SSD_BASE"] = str(cache_root)
    env["SMART_UV_MOVE_VENV"] = "1"

    payload = _run_and_parse([str(bin_dir / "uv"), "sync"], env=env, cwd=nested)
    root_id = client_proxy.sha1_12(str(project_root.resolve()))

    assert payload["argv"][1:] == ["sync"]
    assert payload["env"]["UV_CACHE_DIR"] == str(cache_root / "uv-cache")
    assert payload["env"]["UV_PROJECT_ENVIRONMENT"] == str(cache_root / "uv-venvs" / root_id)


@pytest.mark.e2e
def test_unmounted_path_does_not_set_cache_variables(tmp_path: Path) -> None:
    bin_dir, real_bin, _cache_root = _prepare_tree(tmp_path)
    unmounted = tmp_path / "not-a-mount"
    unmounted.mkdir()

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = str(unmounted)

    payload = _run_and_parse([str(bin_dir / "npm"), "install"], env=env, cwd=tmp_path)
    assert payload["env"]["npm_config_cache"] is None


@pytest.mark.e2e
def test_env_file_precedence_and_explicit_file_override(tmp_path: Path) -> None:
    bin_dir, real_bin, cache_root = _prepare_tree(tmp_path)
    from_example = cache_root / "example"
    from_env = cache_root / "env"
    from_shell = cache_root / "shell"
    from_custom = cache_root / "custom"

    (bin_dir / ".env.example").write_text(
        f"SMART_SSD_MOUNT=/\nSMART_SSD_BASE={from_example}\n",
        encoding="utf-8",
    )
    (bin_dir / ".env").write_text(f"SMART_SSD_BASE={from_env}\n", encoding="utf-8")
    custom_env = tmp_path / "custom.env"
    custom_env.write_text(
        f"SMART_SSD_MOUNT=/\nSMART_SSD_BASE={from_custom}\n",
        encoding="utf-8",
    )

    base_env = dict(os.environ)
    base_env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{base_env.get('PATH', '')}"

    payload_from_env = _run_and_parse([str(bin_dir / "npm"), "install"], env=base_env, cwd=tmp_path)
    assert payload_from_env["env"]["npm_config_cache"] == str(from_env / "npm-cache")

    with_shell_override = dict(base_env)
    with_shell_override["SMART_SSD_BASE"] = str(from_shell)
    payload_shell_override = _run_and_parse(
        [str(bin_dir / "npm"), "install"], env=with_shell_override, cwd=tmp_path
    )
    assert payload_shell_override["env"]["npm_config_cache"] == str(from_shell / "npm-cache")

    with_custom_file = dict(base_env)
    with_custom_file["SMART_SSD_ENV_FILE"] = str(custom_env)
    payload_custom_file = _run_and_parse(
        [str(bin_dir / "npm"), "install"], env=with_custom_file, cwd=tmp_path
    )
    assert payload_custom_file["env"]["npm_config_cache"] == str(from_custom / "npm-cache")


@pytest.mark.e2e
def test_cargo_invocation_sets_target_dir_when_mounted(tmp_path: Path) -> None:
    bin_dir, real_bin, cache_root = _prepare_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = workspace / "Cargo.toml"
    manifest.write_text("[package]\nname='demo'\n", encoding="utf-8")
    rustc_vv = "rustc 1.82.0-nightly (abc123 2025-01-01)"

    home_dir = tmp_path / "home"
    _prepare_rustup_proxies(home_dir, manifest, rustc_vv)

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = "/"
    env["SMART_SSD_BASE"] = str(cache_root)

    payload = _run_and_parse([str(bin_dir / "cargo"), "build"], env=env, cwd=workspace)
    workspace_hash = client_proxy.sha1_12(str(workspace.resolve()))
    tool_hash = client_proxy.sha1_12_bytes(f"{rustc_vv}\n".encode())
    expected = cache_root / "cargo-targets" / f"{workspace_hash}-{tool_hash}"

    assert payload["argv"][1:] == ["build"]
    assert payload["env"]["CARGO_TARGET_DIR"] == str(expected)


@pytest.mark.e2e
def test_cargo_toolchain_prefix_is_preserved(tmp_path: Path) -> None:
    bin_dir, real_bin, cache_root = _prepare_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = workspace / "Cargo.toml"
    manifest.write_text("[package]\nname='demo'\n", encoding="utf-8")
    rustc_vv = "rustc 1.82.0-nightly (abc123 2025-01-01)"

    home_dir = tmp_path / "home"
    _prepare_rustup_proxies(home_dir, manifest, rustc_vv)

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = "/"
    env["SMART_SSD_BASE"] = str(cache_root)

    payload = _run_and_parse([str(bin_dir / "cargo"), "+nightly", "build"], env=env, cwd=workspace)
    assert payload["argv"][1:] == ["+nightly", "build"]


@pytest.mark.e2e
def test_cargo_missing_rustup_proxy_exits_127(tmp_path: Path) -> None:
    bin_dir, real_bin, _cache_root = _prepare_tree(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text("[package]\nname='demo'\n", encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home-without-proxy")
    env["PATH"] = f"{bin_dir}{os.pathsep}{real_bin}{os.pathsep}{env.get('PATH', '')}"
    env["SMART_SSD_MOUNT"] = "/"

    proc = subprocess.run(
        [str(bin_dir / "cargo"), "build"],
        env=env,
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 127
    assert "expected rustup cargo" in proc.stderr
