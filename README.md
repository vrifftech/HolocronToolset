# HolocronToolset

Holocron Toolset is a standalone mod tool and editor for Knights of the
Old Republic resources.

## Setup

Running from source requires Python 3.10 or newer and a compatible PyKotor
checkout containing:

```text
Libraries/PyKotor/src/pykotor
Libraries/Utility/src/utility
Libraries/PyKotorGL/src/pykotor
```

Install the frontend dependencies:

```bash
python -m pip install -r requirements.txt
```

For offline installation, add `--no-index --find-links /path/to/wheels`.

Select the backend explicitly with `HOLOCRON_PYKOTOR_ROOT` or `PYKOTOR_ROOT`.
An adjacent checkout named `PyKotor` or `PyKotor-master` is also recognized.
The application uses that source checkout rather than a pip-installed backend.

On Windows:

```powershell
$env:HOLOCRON_PYKOTOR_ROOT = "C:\src\PyKotor"
python .\run.py --backend-info
python .\run.py
```

On Linux/macOS:

```bash
export HOLOCRON_PYKOTOR_ROOT=/path/to/PyKotor
python run.py --backend-info
python run.py
```

`--backend-info` validates the configured library paths without starting the GUI.

## Native executable builds

Native builds uses a fresh-PyInstaller pipeline. The
`fresh-pyinstaller` action in the configured PyInstaller repository compiles
native bootloaders and produces `FRESH_PYINSTALLER_MANIFEST`. Builds validate
every native bootloader against that manifest. UPX is disabled.
PyInstaller 6.x is required; CI uses Python 3.12.

| Platform | Distribution |
| --- | --- |
| Windows x64 | Single-file `.exe` |
| Linux x86-64 | AppImage containing an onedir payload, plus `.AppImage.tar.gz` |
| macOS Intel | Onedir `.app.zip` |
| macOS Apple Silicon | Onedir `.app.zip` |


### Local Windows build

Windows builds require a PyKotor checkout and a Python environment containing
the custom PyInstaller build. Install application dependencies **before**
running the fresh-PyInstaller helper or installing its native wheel:

```powershell
python -m pip install -r requirements-build.txt
# Run the fresh-pyinstaller helper or install the resulting native wheel.
$env:FRESH_PYINSTALLER_MANIFEST = "C:\build-tools\fresh-pyinstaller.json"
.\build_windows.ps1 -PyKotorRoot "C:\src\PyKotor"
```

Set the manifest path to the file produced by the PyInstaller build. The helper
produces `dist/HolocronToolset.exe` and checks its embedded payload, compiler and startup.

### Local release packages

With the custom PyInstaller build installed, its manifest configured, and
`HOLOCRON_PYKOTOR_ROOT` set, run the matching native target:

```bash
python build/build_distributions.py --target windows-x64
python build/build_distributions.py --target linux-x86_64
python build/build_distributions.py --target macos-x86_64
python build/build_distributions.py --target macos-arm64
```

Each command runs on its target OS/architecture. Use an empty `release/`
directory. Output includes the distribution, SHA-256 checksums, dependency
versions, the PyInstaller manifest, and frontend/backend Git commits when available.
Source archives record `null` for unavailable Git commits.

Linux needs a compiler, zlib headers, `patchelf`, `desktop-file-validate`, and
Qt/OpenGL/XCB libraries; the workflow installs these and runs under Xvfb.
AppImage assembly uses the two URL/hash pins in `build/appimage-tools.json`.
macOS uses ad-hoc signing with the selected deployment target; these builds
are not notarized. Release signing credentials are not configured.

Packaging checks run the executable outside the source tree with invalid
backend environment paths, compile/analyze a script for K1 and K2, verify help,
and test GUI startup. `--no-gui-smoke` skips only GUI startup for local
headless diagnostics; the frozen backend/compiler checks still run.

The frontend compiler hook preserves the additional PLY grammar defined in
`toolset.data.script_analysis`. Build-policy tests can run without Qt or an
installed PyInstaller:

```bash
python -m unittest discover -s tests/packaging -p "test_*.py" -v
```

## Development

- `src/toolset/`: application code, generated UI modules, and help.
- `src/ui/`: editable Qt Designer forms and the regeneration script.
- `src/resources/`: Qt resource declarations, icons, and artwork.
- `tests/`: GUI tests and resource fixtures.

Regenerate UI/resource modules with PyQt5 installed:

```bash
python src/ui/convertui.py
```

Run the tests with the configured PyKotor checkout and development dependencies:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

Set `K1_PATH` and `K2_PATH` when testing against local game installations.
Qt/OpenGL interactions and executable builds require their platform dependencies.


The Help menu opens bundled documentation and links to the project and communities.

See `LICENSE` for licensing.
