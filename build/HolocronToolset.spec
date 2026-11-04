# -*- mode: python ; coding: utf-8 -*-
"""Freeze this frontend with the selected backend and verified fork bootloaders."""
import os
import sys
from pathlib import Path

import PyInstaller

root = Path(SPEC).resolve().parent.parent
sys.path.insert(0, str(root / "build"))
from build_policy import require_build_mode, verify_pyinstaller
from version_info import make_windows_version_info, project_version

mode = os.environ.get("FRONTEND_BUILD_MODE", "onefile" if os.name == "nt" else "onedir").strip().lower()
allowed_mode = "onefile" if os.name == "nt" else "onedir"
os.environ["FRONTEND_BUILD_MODE"] = mode
require_build_mode(allowed_mode)
verify_pyinstaller(Path(PyInstaller.__file__).resolve().parent, PyInstaller.PLATFORM, PyInstaller.__version__)

backend = Path(os.environ["HOLOCRON_PYKOTOR_ROOT"]).expanduser().resolve()
backend_hooks = backend / "Libraries/PyKotor/src/pykotor/__pyinstaller"
if not (backend_hooks / "hook-pykotor.resource.formats.ncs.compiler.py").is_file():
    raise SystemExit("Select the PyKotor checkout containing its PLY compiler packaging hook")
libraries = ["PyKotor", "Utility", "PyKotorGL"]
sources = [root / "src"] + [backend / "Libraries" / library / "src" for library in libraries]
for source in sources:
    if not source.is_dir():
        raise SystemExit(f"Missing source directory: {source}")
    sys.path.insert(0, str(source))
from toolset.bootstrap import bootstrap_backend

bootstrap_backend()

# CLI-selected editors and Qt-generated form imports must remain available.
hidden = ["ply.lex", "ply.yacc"]
if sys.platform.startswith("linux"):
    # PyOpenGL selects GLX or EGL from the user's display session at runtime.
    hidden.extend(["OpenGL.platform.glx", "OpenGL.platform.egl"])
for path in (root / "src/toolset").rglob("*.py"):
    relative = path.relative_to(root / "src")
    if any(part in {"__pycache__", "help"} for part in relative.parts[:-1]):
        continue
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    hidden.append(".".join(parts))
datas = [(str(root / "src/toolset/help"), "toolset/help"),
         (str(root / "LICENSE"), "licenses/frontend")]
for relative, destination in [("LICENSE", "licenses/backend")] + [
    (f"Libraries/{library}/LICENSE", f"licenses/{library}") for library in libraries
]:
    license_path = backend / relative
    if license_path.is_file():
        datas.append((str(license_path), destination))

a = Analysis([str(root / "build/entry.py")], pathex=[str(p) for p in sources],
             binaries=[], datas=datas, hiddenimports=sorted(set(hidden)),
             hookspath=[str(root / "build/hooks"), str(backend_hooks)],
             hooksconfig={}, runtime_hooks=[],
             excludes=["PyQt6", "PySide2", "PySide6", "IPython", "pip", "pytest", "setuptools", "wheel"],
             noarchive=False)
pyz = PYZ(a.pure)
# GNU strip 2.38 corrupts the ELF layout of NumPy's bundled OpenBLAS library.
strip_enabled = sys.platform == "darwin"
version = project_version(root)
options = dict(name="HolocronToolset", debug=False, bootloader_ignore_signals=False,
               strip=strip_enabled, upx=False, console=sys.platform.startswith("linux"),
               icon=str(root / "src/resources/icons/sith.ico") if os.name == "nt" else None)
if os.name == "nt":
    options["version"] = make_windows_version_info(version)
if mode == "onefile":
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], exclude_binaries=False, append_pkg=True, **options)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, append_pkg=True, **options)
    coll = COLLECT(exe, a.binaries, a.datas, strip=strip_enabled, upx=False, name="HolocronToolset")

if sys.platform == "darwin":
    from packaging.version import Version

    release_version = ".".join(str(part) for part in Version(version).release)
    app = BUNDLE(coll, name="HolocronToolset.app",
                 icon=str(root / "src/resources/icons/sith.png"),
                 bundle_identifier="org.pykotor.holocrontoolset",
                 info_plist={"CFBundleShortVersionString": release_version,
                             "CFBundleVersion": release_version,
                             "CFBundleGetInfoString": f"Holocron Toolset {version}",
                             "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET") or "15.0",
                             "LSBackgroundOnly": False,
                             "NSPrincipalClass": "NSApplication",
                             "NSHighResolutionCapable": True})
