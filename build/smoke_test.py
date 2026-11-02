"""Verify the frozen backend/compiler, then check Qt startup without a source checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile


def _stop_gui(process: subprocess.Popen) -> None:
    """Stop the launcher and its children, then drain inherited stderr handles."""
    if os.name == "nt":
        if process.poll() is None:
            stopped = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                timeout=15, check=False,
            )
            if stopped.returncode != 0 and process.poll() is None:
                raise RuntimeError(f"Unable to stop GUI process tree: {stopped.stderr}")
        process.communicate(timeout=10)
        return

    # A separate session also contains AppImage launchers and their children.
    for stop_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, stop_signal)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=10)
            return
        except subprocess.TimeoutExpired:
            if stop_signal == signal.SIGKILL:
                raise


def smoke_test(executable: Path, *, gui: bool = True, appimage: bool = False) -> dict:
    executable = executable.resolve()
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(key, None)
    env["HOLOCRON_PYKOTOR_ROOT"] = "__invalid_backend_must_not_be_used__"
    env["PYKOTOR_ROOT"] = "__invalid_backend_must_not_be_used__"
    if appimage:
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    else:
        env.pop("APPIMAGE_EXTRACT_AND_RUN", None)
    with tempfile.TemporaryDirectory(prefix="Holocron packaging check ") as directory:
        work = Path(directory)
        report = work / "frozen-check.json"
        completed = subprocess.run([str(executable), "--packaging-check", str(report)], cwd=work, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   check=False, timeout=120)
        if not report.is_file():
            raise RuntimeError(f"Frozen executable produced no packaging report (exit {completed.returncode})")
        result = json.loads(report.read_text(encoding="utf-8"))
        if completed.returncode != 0 or result.get("ok") is not True or result.get("frozen") is not True:
            raise RuntimeError(f"Frozen packaging check failed: {result}")
        if gui:
            env["XDG_CONFIG_HOME"] = str(work / "config")
            env["QT_DEBUG_PLUGINS"] = "1"
            # An inherited file handle remains locked while a Windows onefile
            # child is alive. A pipe captures diagnostics without locking a file
            # in the temporary directory, and communicate drains it as it runs.
            process = subprocess.Popen(
                [str(executable)], cwd=work, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                start_new_session=os.name != "nt",
            )
            try:
                try:
                    _, errors = process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    pass  # The application stayed open throughout the check.
                else:
                    raise RuntimeError(f"GUI exited during startup with code {process.returncode}: {errors}")
            finally:
                _stop_gui(process)
    checks = "Frozen backend/compiler and GUI startup" if gui else "Frozen backend/compiler"
    print(f"{checks} checks passed: {executable}", flush=True)
    return result
