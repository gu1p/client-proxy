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

    def test_requires_at_least_argv0(self) -> None:
        with self.assertRaises(ValueError):
            client_proxy.determine_tool_and_args([])


class SettingsTests(unittest.TestCase):
    def test_from_runtime_reads_env_files_and_allows_shell_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "client-proxy"
            wrapper.write_text("#!/bin/sh\n")
            (Path(tmp) / ".env.example").write_text(
                "SMART_SSD_MOUNT=/\nSMART_SSD_BASE=/Volumes/SSD-EXAMPLE/dev-caches\n",
            )
            settings = client_proxy.Settings.from_runtime(
                str(wrapper),
                {"SMART_SSD_BASE": "/Volumes/SSD-SHELL/dev-caches"},
            )
        self.assertEqual(settings.smart_ssd_mount, "/")
        self.assertEqual(settings.smart_ssd_base, "/Volumes/SSD-SHELL/dev-caches")

    def test_from_runtime_rejects_invalid_boolean_values(self) -> None:
        with self.assertRaises(ValueError):
            client_proxy.Settings.from_runtime("client-proxy", {"SMART_UV_MOVE_VENV": "banana"})

    def test_cache_base_dir_falls_back_to_mount(self) -> None:
        settings = client_proxy.Settings(smart_ssd_mount="/Volumes/SSD3")
        self.assertEqual(settings.cache_base_dir, Path("/Volumes/SSD3/dev-caches"))


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
                f"SMART_SSD_MOUNT=/Volumes/SSD-FROM-FILE\nSMART_SSD_BASE={tmp}/caches\n"
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


class UtilityEdgeCaseTests(unittest.TestCase):
    def test_log_writes_when_debug_enabled(self) -> None:
        stderr = StringIO()
        settings = client_proxy.Settings(smart_ssd_debug=True)
        with patch("sys.stderr", stderr):
            client_proxy.log("hello", settings=settings)
        self.assertIn("[client-proxy] hello", stderr.getvalue())

    def test_parse_env_file_ignores_invalid_lines_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("NO_EQUALS\n=missing_key\nVALID=value\n", encoding="utf-8")
            values = client_proxy.parse_env_file(env_file)
            missing = client_proxy.parse_env_file(Path(tmp) / ".missing")
        self.assertEqual(values, {"VALID": "value"})
        self.assertEqual(missing, {})

    def test_wrapper_dirs_deduplicates_real_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / "client-proxy"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            with patch("main.wrapper_realpath", return_value=str(wrapper)):
                dirs = client_proxy.wrapper_dirs_for_env_files(str(wrapper), {"PATH": tmp})
        self.assertEqual(dirs, [Path(tmp)])

    def test_find_project_root_returns_none_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "a" / "b"
            nested.mkdir(parents=True)
            self.assertIsNone(client_proxy.find_project_root_for_uv(nested))

    def test_find_real_executable_handles_empty_path_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wrapper = base / "client-proxy"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            wrapper.chmod(0o755)
            real = base / "uv"
            real.write_text("#!/bin/sh\n", encoding="utf-8")
            real.chmod(0o755)

            old_cwd = Path.cwd()
            try:
                os.chdir(base)
                found = client_proxy.find_real_executable("uv", str(wrapper), path_env=f":{tmp}")
                resolved_found = os.path.realpath(found or "")
            finally:
                os.chdir(old_cwd)

        self.assertIsNotNone(found)
        self.assertEqual(resolved_found, str(real.resolve()))

    def test_is_mounted_handles_ismount_exception_and_fallback_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount_path = Path(tmp) / "mount"
            mount_path.mkdir()
            with (
                patch("main.os.path.ismount", side_effect=OSError("boom")),
                patch(
                    "main.subprocess.check_output", return_value=f"disk on {mount_path} type apfs"
                ),
            ):
                self.assertTrue(client_proxy.is_mounted(str(mount_path)))

    def test_is_mounted_handles_mount_command_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mount_path = Path(tmp) / "mount"
            mount_path.mkdir()
            with (
                patch("main.os.path.ismount", return_value=False),
                patch("main.subprocess.check_output", side_effect=OSError("boom")),
            ):
                self.assertFalse(client_proxy.is_mounted(str(mount_path)))

    def test_is_mounted_returns_false_for_non_directory(self) -> None:
        self.assertFalse(client_proxy.is_mounted("/definitely/not/a/real/path"))


class HandlerEdgeCaseTests(unittest.TestCase):
    def test_uv_handler_does_not_set_project_env_without_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "cache-root"
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            env: dict[str, str] = {}
            settings = client_proxy.Settings(smart_uv_move_venv=True)

            old_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                client_proxy.UvCliHandler.configure(env, base, settings)
            finally:
                os.chdir(old_cwd)

            self.assertEqual(env["UV_CACHE_DIR"], str(base / "uv-cache"))
            self.assertNotIn("UV_PROJECT_ENVIRONMENT", env)

    def test_apply_env_for_unknown_tool_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            settings = client_proxy.Settings(smart_ssd_mount="/", smart_ssd_base=str(base))
            env: dict[str, str] = {}
            client_proxy.apply_env_for_tool("unknown", env, settings)
            self.assertEqual(env, {})
            self.assertTrue(base.is_dir())


class RunTests(unittest.TestCase):
    def test_run_exits_with_main_return_code(self) -> None:
        with patch("main.main", return_value=5), self.assertRaises(SystemExit) as ctx:
            client_proxy.run()
        self.assertEqual(ctx.exception.code, 5)


class MainErrorPathsTests(unittest.TestCase):
    def test_main_returns_2_when_settings_are_invalid(self) -> None:
        stderr = StringIO()
        with (
            patch("main.Settings.from_runtime", side_effect=ValueError("bad settings")),
            patch("sys.stderr", stderr),
        ):
            rc = client_proxy.main(["client-proxy", "npm", "install"])

        self.assertEqual(rc, 2)
        self.assertIn("bad settings", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
