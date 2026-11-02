"""Verify the frozen backend/compiler, then check Qt startup without a source checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


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
            with (work / "startup-stderr.txt").open("w+", encoding="utf-8") as errors:
                process = subprocess.Popen([str(executable)], cwd=work, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=errors)
                try:
                    time.sleep(3)
                    if process.poll() is not None:
                        errors.seek(0)
                        raise RuntimeError(f"GUI exited during startup with code {process.returncode}: {errors.read()}")
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
    checks = "Frozen backend/compiler and GUI startup" if gui else "Frozen backend/compiler"
    print(f"{checks} checks passed: {executable}", flush=True)
    return result
