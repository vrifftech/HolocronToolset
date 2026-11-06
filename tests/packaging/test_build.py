"""Build-policy/spec tests that need neither Qt nor an installed PyInstaller."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import runpy
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build"))
import build_policy as policy
import build_distributions as distributions
import smoke_test
import version_info


class BuildFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.package = self.folder / "PyInstaller"
        self.manifest = self.folder / "fresh-pyinstaller.json"

    def fresh(self, system="Linux", version="6.22.0"):
        names = {"run", "run_d"}
        if system in {"Windows", "Darwin"}:
            names |= {"runw", "runw_d"}
        if system == "Windows":
            names = {name + ".exe" for name in names}
        native = "test-native-" + system
        hashes = {}
        for name in names:
            path = self.package / "bootloader" / native / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((system + name).encode())
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        data = dict(schema_version=1, system=system, pyinstaller_platform=native,
                    pyinstaller_version=version, bootloaders_sha256=hashes)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        return native, data


class ManifestTests(BuildFixture):
    def test_all_supported_native_bootloaders_are_verified(self):
        for system in ("Windows", "Linux", "Darwin"):
            native, data = self.fresh(system)
            with self.subTest(system=system), patch.dict(os.environ, FRESH_PYINSTALLER_MANIFEST=str(self.manifest)), \
                 patch.object(policy.platform, "system", return_value=system):
                self.assertEqual(policy.verify_pyinstaller(self.package, native, "6.22.0"), data)

    def test_missing_manifest_rejects_unverified_pyinstaller(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "MANIFEST is required"):
            policy.verify_pyinstaller(self.package, "native", "6.22.0")

    def test_platform_version_and_schema_must_match(self):
        native, original = self.fresh()
        for field, value in (("system", "Windows"), ("schema_version", 2),
                             ("pyinstaller_version", "6.1"), ("pyinstaller_platform", "other")):
            self.manifest.write_text(json.dumps({**original, field: value}), encoding="utf-8")
            with self.subTest(field=field), patch.dict(os.environ, FRESH_PYINSTALLER_MANIFEST=str(self.manifest)), \
                 patch.object(policy.platform, "system", return_value="Linux"), self.assertRaises(RuntimeError):
                policy.verify_pyinstaller(self.package, native, "6.22.0")

    def test_unused_debug_bootloader_still_must_match(self):
        native, _ = self.fresh("Windows")
        (self.package / "bootloader" / native / "runw_d.exe").write_bytes(b"replaced")
        with patch.dict(os.environ, FRESH_PYINSTALLER_MANIFEST=str(self.manifest)), \
             patch.object(policy.platform, "system", return_value="Windows"), self.assertRaisesRegex(RuntimeError, "replaced"):
            policy.verify_pyinstaller(self.package, native, "6.22.0")

    def test_missing_or_extra_bootloader_records_are_rejected(self):
        native, data = self.fresh()
        del data["bootloaders_sha256"]["run_d"]
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        with patch.dict(os.environ, FRESH_PYINSTALLER_MANIFEST=str(self.manifest)), \
             patch.object(policy.platform, "system", return_value="Linux"), self.assertRaisesRegex(RuntimeError, "exactly all"):
            policy.verify_pyinstaller(self.package, native, "6.22.0")

    def test_new_pyinstaller_major_requires_an_explicit_update(self):
        with self.assertRaisesRegex(RuntimeError, "6.x"):
            policy.verify_pyinstaller(self.package, "native", "7.0")


class PayloadTests(BuildFixture):
    def archive(self, toc):
        module = ModuleType("PyInstaller.archive.readers")
        module.CArchiveReader = lambda path: SimpleNamespace(toc=toc)
        return patch.dict(sys.modules, {module.__name__: module})

    def test_onefile_requires_embedded_native_libraries(self):
        exe = self.folder / "HolocronToolset.exe"
        exe.touch()
        for toc, valid in (({"python.dll": ("b",)}, True), ({"script": ("s",)}, False),
                           ({"python.dll": ("b",), "debug.pdb": ("x",)}, False),
                           ({"python.dll": ("b",), "external": ("d",)}, False)):
            with self.subTest(toc=toc), self.archive(toc):
                if valid:
                    self.assertEqual(policy.verify_onefile_payload(exe)["embedded_native_binaries"], 1)
                else:
                    with self.assertRaises(RuntimeError):
                        policy.verify_onefile_payload(exe)

    def test_onedir_requires_a_launcher_and_support_files(self):
        payload = self.folder / "HolocronToolset"
        payload.mkdir()
        (payload / "HolocronToolset").touch()
        with self.assertRaisesRegex(RuntimeError, "support files"):
            policy.verify_onedir_payload(payload)
        (payload / "libpython.so").write_bytes(b"library")
        self.assertEqual(policy.verify_onedir_payload(payload)["files"], 2)

    def test_mac_app_requires_a_real_payload(self):
        app = self.folder / "HolocronToolset.app"
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "HolocronToolset"}))
        (contents / "MacOS/HolocronToolset").touch()
        with self.assertRaisesRegex(RuntimeError, "support files"):
            policy.verify_app_bundle_payload(app)
        (contents / "library.dylib").touch()
        self.assertEqual(policy.verify_app_bundle_payload(app)["mode"], "app-onedir")


class SpecTests(BuildFixture):
    def spec(self, system, mode=None, *, manifest=True):
        native, _ = self.fresh(system)
        backend = self.folder / "PyKotor"
        for library in ("PyKotor", "Utility", "PyKotorGL"):
            (backend / f"Libraries/{library}/src").mkdir(parents=True, exist_ok=True)
        hook = backend / "Libraries/PyKotor/src/pykotor/__pyinstaller"
        hook.mkdir(parents=True, exist_ok=True)
        (hook / "hook-pykotor.resource.formats.ncs.compiler.py").touch()
        engine = ModuleType("PyInstaller")
        engine.__file__, engine.PLATFORM, engine.__version__ = str(self.package / "__init__.py"), native, "6.22.0"
        fake_os = ModuleType("os")
        fake_os.name, fake_os.environ = "nt" if system == "Windows" else "posix", os.environ
        fake_sys = ModuleType("sys")
        fake_sys.platform = {"Windows": "win32", "Linux": "linux", "Darwin": "darwin"}[system]
        fake_sys.path = list(sys.path)
        bootstrap = ModuleType("toolset.bootstrap")
        bootstrap.bootstrap_backend = Mock(return_value=backend)
        frontend = ModuleType("toolset")
        frontend.__path__ = [str(ROOT / "src/toolset")]
        calls = []
        def make(name):
            def capture(*args, **kwargs):
                calls.append((name, args, kwargs))
                return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs.get("datas", []))
            return capture
        values = {"SPEC": str(ROOT / "build/HolocronToolset.spec"),
                  **{name: make(name) for name in ("Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE")}}
        env = dict(HOLOCRON_PYKOTOR_ROOT=str(backend), MACOSX_DEPLOYMENT_TARGET="15.0")
        if manifest:
            env["FRESH_PYINSTALLER_MANIFEST"] = str(self.manifest)
        if mode is not None:
            env["FRONTEND_BUILD_MODE"] = mode
        modules = {"PyInstaller": engine, "os": fake_os, "sys": fake_sys,
                   "toolset": frontend, "toolset.bootstrap": bootstrap}
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules), \
             patch.object(policy.platform, "system", return_value=system), \
             patch.object(version_info, "make_windows_version_info", return_value="version-info"):
            exec(compile((ROOT / "build/HolocronToolset.spec").read_bytes(), "spec", "exec"), values)
        bootstrap.bootstrap_backend.assert_called_once()
        return calls

    def test_windows_uses_a_windowed_single_file_and_no_upx(self):
        calls = self.spec("Windows")
        exe = next(kwargs for name, _, kwargs in calls if name == "EXE")
        self.assertFalse(exe["exclude_binaries"])
        self.assertFalse(exe["upx"])
        self.assertFalse(exe["console"])
        self.assertEqual(exe["version"], "version-info")
        self.assertNotIn("COLLECT", [name for name, _, _ in calls])

    def test_linux_and_mac_use_onedir_and_mac_bundles_collect(self):
        for system in ("Linux", "Darwin"):
            with self.subTest(system=system):
                calls = self.spec(system)
                exe = next(kwargs for name, _, kwargs in calls if name == "EXE")
                collect = next(kwargs for name, _, kwargs in calls if name == "COLLECT")
                self.assertTrue(exe["exclude_binaries"])
                self.assertFalse(collect["upx"])
                if system == "Darwin":
                    bundle = next(kwargs for name, _, kwargs in calls if name == "BUNDLE")
                    self.assertEqual(bundle["name"], "HolocronToolset.app")
                    self.assertEqual(bundle["info_plist"]["CFBundleShortVersionString"], "4.0.0")

    def test_spec_requires_fork_proof_even_when_invoked_directly(self):
        with self.assertRaisesRegex(RuntimeError, "MANIFEST is required"):
            self.spec("Linux", manifest=False)

    def test_native_modes_cannot_be_overridden(self):
        for system, mode in (("Windows", "onedir"), ("Linux", "onefile"), ("Darwin", "onefile")):
            with self.subTest(system=system), self.assertRaisesRegex(RuntimeError, "FRONTEND_BUILD_MODE"):
                self.spec(system, mode)

    def test_source_hooks_dynamic_editors_and_assets_are_collected(self):
        calls = self.spec("Linux")
        analysis = next(kwargs for name, _, kwargs in calls if name == "Analysis")
        self.assertEqual(len(analysis["hookspath"]), 2)
        self.assertIn("toolset.data.script_analysis", analysis["hiddenimports"])
        self.assertIn("toolset.gui.editors.nss", analysis["hiddenimports"])
        destinations = {destination for _, destination in analysis["datas"]}
        self.assertTrue({"toolset/help", "licenses/frontend"} <= destinations)
        self.assertFalse(any("kits" in Path(destination).parts for destination in destinations))
        self.assertIn("PySide6", analysis["excludes"])
        hook = runpy.run_path(str(ROOT / "build/hooks/hook-toolset.data.script_analysis.py"))
        self.assertEqual(hook["module_collection_mode"], "pyz+py")


class GuiSmokeTests(unittest.TestCase):
    @staticmethod
    def frozen_ok(command, **kwargs):
        Path(command[2]).write_text(json.dumps({"ok": True, "frozen": True}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def test_startup_failure_keeps_the_qt_diagnostic(self):
        process = Mock(returncode=-6)
        process.communicate.return_value = (None, "Could not load Qt platform plugin xcb: missing library")
        with patch.object(smoke_test.subprocess, "run", side_effect=self.frozen_ok), \
             patch.object(smoke_test.subprocess, "Popen", return_value=process) as launch, \
             patch.object(smoke_test, "_stop_gui") as stop, \
             self.assertRaisesRegex(RuntimeError, "xcb: missing library"):
            smoke_test.smoke_test(Path("executable"))
        stop.assert_called_once_with(process)
        self.assertEqual(launch.call_args.kwargs["stderr"], smoke_test.subprocess.PIPE)
        self.assertEqual(launch.call_args.kwargs["env"]["QT_DEBUG_PLUGINS"], "1")

    def test_running_gui_is_stopped_before_the_temporary_directory_is_removed(self):
        process = Mock()
        process.communicate.side_effect = smoke_test.subprocess.TimeoutExpired("GUI", 3)
        def stopped(gui):
            self.assertTrue(Path(launch.call_args.kwargs["cwd"]).is_dir())
        with patch.object(smoke_test.subprocess, "run", side_effect=self.frozen_ok), \
             patch.object(smoke_test.subprocess, "Popen", return_value=process) as launch, \
             patch.object(smoke_test, "_stop_gui", side_effect=stopped) as stop:
            self.assertTrue(smoke_test.smoke_test(Path("executable"))["ok"])
        stop.assert_called_once_with(process)
        self.assertFalse(Path(launch.call_args.kwargs["cwd"]).exists())

    def test_windows_cleanup_terminates_launcher_and_children(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        with patch.object(smoke_test.os, "name", "nt"), \
             patch.object(smoke_test.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as taskkill:
            smoke_test._stop_gui(process)
        self.assertEqual(taskkill.call_args.args[0], ["taskkill", "/PID", "12345", "/T", "/F"])
        process.communicate.assert_called_once_with(timeout=10)

    def test_failed_windows_tree_cleanup_is_reported(self):
        process = Mock(pid=12345)
        process.poll.return_value = None
        result = SimpleNamespace(returncode=1, stderr="Access is denied")
        with patch.object(smoke_test.os, "name", "nt"), \
             patch.object(smoke_test.subprocess, "run", return_value=result), \
             self.assertRaisesRegex(RuntimeError, "Access is denied"):
            smoke_test._stop_gui(process)

    def test_posix_cleanup_escalates_for_children_that_ignore_termination(self):
        process = Mock(pid=12345)
        process.communicate.side_effect = [smoke_test.subprocess.TimeoutExpired("GUI", 10), (None, "")]
        signals = SimpleNamespace(SIGTERM=15, SIGKILL=9)
        with patch.object(smoke_test.os, "name", "posix"), \
             patch.object(smoke_test, "signal", signals), \
             patch.object(smoke_test.os, "killpg", create=True) as kill_group:
            smoke_test._stop_gui(process)
        self.assertEqual(kill_group.call_args_list, [call(12345, 15), call(12345, 9)])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_real_child_holding_stderr_is_stopped_with_its_parent(self):
        import select

        command = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print('ready', file=sys.stderr, flush=True); time.sleep(60)"
        )
        process = smoke_test.subprocess.Popen(
            [sys.executable, "-c", command], stdin=smoke_test.subprocess.DEVNULL,
            stdout=smoke_test.subprocess.DEVNULL, stderr=smoke_test.subprocess.PIPE,
            text=True, start_new_session=True,
        )
        try:
            ready, _, _ = select.select([process.stderr], [], [], 5)
            self.assertTrue(ready, "Process fixture did not start")
            self.assertEqual(process.stderr.readline(), "ready\n")
            smoke_test._stop_gui(process)
            self.assertIsNotNone(process.returncode)
            self.assertTrue(process.stderr.closed)
        finally:
            try:
                os.killpg(process.pid, smoke_test.signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=5)


class DistributionTests(unittest.TestCase):
    def test_freeze_uses_the_current_interpreter_and_local_spec(self):
        with patch.dict(os.environ, FRONTEND_BUILD_MODE="onedir"), patch.object(distributions, "run") as run:
            distributions.freeze("onedir")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, "-m", "PyInstaller"])
        self.assertEqual(command[-1], str(ROOT / "build/HolocronToolset.spec"))

    def test_freeze_rejects_wrong_mode_before_starting_pyinstaller(self):
        with patch.dict(os.environ, FRONTEND_BUILD_MODE="onefile"), patch.object(distributions, "run") as run:
            with self.assertRaises(RuntimeError):
                distributions.freeze("onedir")
        run.assert_not_called()

    def test_headless_smoke_still_checks_the_frozen_compiler(self):
        def complete(command, **kwargs):
            self.assertEqual(command[1], "--packaging-check")
            self.assertEqual(kwargs["env"]["HOLOCRON_PYKOTOR_ROOT"], "__invalid_backend_must_not_be_used__")
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            Path(command[2]).write_text(json.dumps({"ok": True, "frozen": True}), encoding="utf-8")
            return SimpleNamespace(returncode=0)
        with patch.object(smoke_test.subprocess, "run", side_effect=complete) as run, \
             patch.object(smoke_test.subprocess, "Popen") as popen:
            self.assertTrue(smoke_test.smoke_test(Path("not a real executable"), gui=False)["ok"])
        run.assert_called_once()
        popen.assert_not_called()

    def test_project_version_matches_the_frontend(self):
        self.assertEqual(version_info.project_version(ROOT), "4.0.0b5")

    def test_failed_frozen_check_keeps_its_diagnostic(self):
        def failed(command, **kwargs):
            Path(command[2]).write_text(json.dumps({"ok": False, "error": "grammar source missing"}), encoding="utf-8")
            return SimpleNamespace(returncode=1)
        with patch.object(smoke_test.subprocess, "run", side_effect=failed), \
             self.assertRaisesRegex(RuntimeError, "grammar source missing"):
            smoke_test.smoke_test(Path("executable"), gui=False)

    def test_windows_version_preserves_prerelease_information(self):
        module = ModuleType("PyInstaller.utils.win32.versioninfo")
        for name in ("FixedFileInfo", "StringFileInfo", "StringStruct", "StringTable", "VarFileInfo", "VarStruct", "VSVersionInfo"):
            setattr(module, name, lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs))
        with patch.dict(sys.modules, {module.__name__: module}):
            result = version_info.make_windows_version_info("4.0.0b5")
        ffi = result.kwargs["ffi"].kwargs
        self.assertEqual(ffi["filevers"], (4, 0, 0, 0))
        self.assertEqual(ffi["flags"], 0x02)

    def test_unrepresentable_windows_versions_are_rejected(self):
        for version in ("1!4.0", "1.2.3.4.5", "65536.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                version_info.make_windows_version_info(version)

    def test_stock_pyinstaller_is_not_in_build_requirements(self):
        for name in ("requirements-build.txt", "requirements.txt"):
            requirements = [line.strip().lower() for line in (ROOT / name).read_text().splitlines()
                            if line.strip() and not line.lstrip().startswith("#")]
            self.assertFalse(any(line.startswith("pyinstaller") for line in requirements))


if __name__ == "__main__":
    unittest.main()
