from __future__ import annotations

import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import main as client_proxy


class DetermineToolAndArgsTests(unittest.TestCase):
    def test_parses_client_proxy_style_invocation(self) -> None:
        tool, args = client_proxy.determine_tool_and_args(
            ["client-proxy", "uv", "sync", "--frozen"]
        )
        self.assertEqual(tool, "uv")
        self.assertEqual(args, ["sync", "--frozen"])

    def test_parses_legacy_ssdwrap_style_invocation(self) -> None:
        tool, args = client_proxy.determine_tool_and_args(["ssdwrap", "uv", "sync"])
        self.assertEqual(tool, "uv")
        self.assertEqual(args, ["sync"])

    def test_parses_symlink_style_invocation(self) -> None:
        tool, args = client_proxy.determine_tool_and_args(["/usr/local/bin/npm", "install"])
        self.assertEqual(tool, "npm")
        self.assertEqual(args, ["install"])

    def test_requires_tool_when_invoked_as_wrapper(self) -> None:
        with self.assertRaises(ValueError):
            client_proxy.determine_tool_and_args(["client-proxy"])


class EnvFileTests(unittest.TestCase):
    def test_parse_env_file_supports_export_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env.example"
            env_file.write_text(
                "\n".join(
                    [
                        "# comment",
                        "SMART_SSD_MOUNT=/Volumes/SSD2",
                        "export SMART_SSD_DEBUG=1",
                        "SMART_PNPM_IMPORT_METHOD='hardlink'",
                        'SMART_SSD_BASE="/Volumes/SSD2/dev-caches"',
                    ]
                )
            )
            values = client_proxy.parse_env_file(env_file)

        self.assertEqual(values["SMART_SSD_MOUNT"], "/Volumes/SSD2")
        self.assertEqual(values["SMART_SSD_DEBUG"], "1")
        self.assertEqual(values["SMART_PNPM_IMPORT_METHOD"], "hardlink")
        self.assertEqual(values["SMART_SSD_BASE"], "/Volumes/SSD2/dev-caches")

    def test_load_env_file_defaults_uses_env_over_example_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "client-proxy"
            wrapper.write_text("#!/bin/sh\n")
            (Path(tmp) / ".env.example").write_text(
                "SMART_SSD_MOUNT=/Volumes/SSD-EXAMPLE\nSMART_SSD_DEBUG=1\n"
            )
            (Path(tmp) / ".env").write_text("SMART_SSD_MOUNT=/Volumes/SSD-REAL\n")

            values = client_proxy.load_env_file_defaults(str(wrapper), {})

        self.assertEqual(values["SMART_SSD_MOUNT"], "/Volumes/SSD-REAL")
        self.assertEqual(values["SMART_SSD_DEBUG"], "1")

    def test_load_env_file_defaults_supports_explicit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "client-proxy"
            wrapper.write_text("#!/bin/sh\n")
            explicit = Path(tmp) / "custom.env"
            explicit.write_text("SMART_SSD_MOUNT=/Volumes/SSD-CUSTOM\n")
            values = client_proxy.load_env_file_defaults(
                str(wrapper), {"SMART_SSD_ENV_FILE": str(explicit)}
            )
        self.assertEqual(values["SMART_SSD_MOUNT"], "/Volumes/SSD-CUSTOM")

    def test_load_env_file_defaults_prefers_invoked_wrapper_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src_dir = base / "src"
            bin_dir = base / "bin"
            src_dir.mkdir()
            bin_dir.mkdir()

            script = src_dir / "main.py"
            script.write_text("#!/usr/bin/env python3\n")
            wrapper_link = bin_dir / "client-proxy"
            wrapper_link.symlink_to(script)
            uv_link = bin_dir / "uv"
            uv_link.symlink_to(wrapper_link)

            (src_dir / ".env.example").write_text("SMART_SSD_MOUNT=/Volumes/SSD-SRC\n")
            (bin_dir / ".env.example").write_text("SMART_SSD_MOUNT=/Volumes/SSD-BIN\n")

            values = client_proxy.load_env_file_defaults(str(uv_link), {"PATH": str(bin_dir)})

        self.assertEqual(values["SMART_SSD_MOUNT"], "/Volumes/SSD-BIN")


class FindRealExecutableTests(unittest.TestCase):
    def test_skips_wrapper_binary_and_finds_next_match(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            wrapper = Path(first) / "uv"
            wrapper.write_text("#!/bin/sh\n")
            wrapper.chmod(0o755)

            real = Path(second) / "uv"
            real.write_text("#!/bin/sh\n")
            real.chmod(0o755)

            with patch.dict(os.environ, {"PATH": f"{first}{os.pathsep}{second}"}, clear=False):
                found = client_proxy.find_real_executable("uv", str(wrapper))

            self.assertEqual(found, str(real))


class HandlerTests(unittest.TestCase):
    def test_uv_handler_sets_cache_and_optional_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "cache-root"
            project = Path(tmp) / "project"
            nested = project / "src"
            nested.mkdir(parents=True, exist_ok=True)
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n")

            env = {"SMART_UV_MOVE_VENV": "1"}
            old_cwd = Path.cwd()
            try:
                os.chdir(nested)
                client_proxy.UvCliHandler.configure(env, base)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(env["UV_CACHE_DIR"], str(base / "uv-cache"))
            rid = client_proxy.sha1_12(str(project.resolve()))
            self.assertEqual(env["UV_PROJECT_ENVIRONMENT"], str(base / "uv-venvs" / rid))
            self.assertTrue((base / "uv-cache").is_dir())
            self.assertTrue((base / "uv-venvs" / rid).is_dir())

    def test_npm_handler_sets_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "cache-root"
            env: dict[str, str] = {}
            client_proxy.NpmCliHandler.configure(env, base)
            self.assertEqual(env["npm_config_cache"], str(base / "npm-cache"))
            self.assertTrue((base / "npm-cache").is_dir())

    def test_pnpm_handler_sets_store_and_import_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "cache-root"
            env = {"SMART_PNPM_IMPORT_METHOD": "hardlink"}
            client_proxy.PnpmCliHandler.configure(env, base)
            self.assertEqual(env["npm_config_store_dir"], str(base / "pnpm-store"))
            self.assertEqual(env["npm_config_package_import_method"], "hardlink")
            self.assertTrue((base / "pnpm-store").is_dir())

    def test_pnpm_handler_does_not_set_import_method_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "cache-root"
            env: dict[str, str] = {}
            client_proxy.PnpmCliHandler.configure(env, base)
            self.assertEqual(env["npm_config_store_dir"], str(base / "pnpm-store"))
            self.assertNotIn("npm_config_package_import_method", env)


class MainFlowTests(unittest.TestCase):
    def test_main_execs_tool_with_modified_env_when_mounted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            env = {"SMART_SSD_BASE": str(base), "PATH": "/usr/bin"}

            with (
                patch.dict(os.environ, env, clear=False),
                patch("main.is_mounted", return_value=True),
                patch("main.find_real_executable", return_value="/usr/bin/npm"),
                patch("main.os.execvpe") as execvpe,
            ):
                rc = client_proxy.main(["client-proxy", "npm", "install"])

            self.assertEqual(rc, 0)
            self.assertEqual(execvpe.call_count, 1)
            called_real, called_argv, called_env = execvpe.call_args[0]
            self.assertEqual(called_real, "/usr/bin/npm")
            self.assertEqual(called_argv, ["/usr/bin/npm", "install"])
            self.assertEqual(called_env["npm_config_cache"], str(base / "npm-cache"))

    def test_main_uses_env_file_defaults_without_exported_env_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "client-proxy"
            wrapper.write_text("#!/bin/sh\n")
            (Path(tmp) / ".env.example").write_text(
                "SMART_SSD_MOUNT=/Volumes/SSD-FROM-FILE\n"
                f"SMART_SSD_BASE={tmp}/caches\n"
            )
            with (
                patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False),
                patch("main.is_mounted", return_value=True) as is_mounted,
                patch("main.find_real_executable", return_value="/usr/bin/npm"),
                patch("main.os.execvpe") as execvpe,
            ):
                rc = client_proxy.main([str(wrapper), "npm", "install"])

            self.assertEqual(rc, 0)
            is_mounted.assert_called_once_with("/Volumes/SSD-FROM-FILE")
            called_env = execvpe.call_args[0][2]
            self.assertEqual(called_env["npm_config_cache"], f"{tmp}/caches/npm-cache")

    def test_main_returns_127_when_tool_is_missing(self) -> None:
        stderr = StringIO()
        with (
            patch("main.is_mounted", return_value=False),
            patch("main.find_real_executable", return_value=None),
            patch("sys.stderr", stderr),
        ):
            rc = client_proxy.main(["uv", "sync"])

        self.assertEqual(rc, 127)
        self.assertIn("can't find real 'uv'", stderr.getvalue())

    def test_main_returns_2_on_usage_error(self) -> None:
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            rc = client_proxy.main(["client-proxy"])
        self.assertEqual(rc, 2)
        self.assertIn("Usage: client-proxy <uv|npm|pnpm>", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
