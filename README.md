# HolocronToolset

Holocron Toolset **4.0.0b** is a standalone PyQt5 editor for Knights of the
Old Republic resources. Its Python package version is `4.0.0b0`.

## Setup

Use Python 3.10 or newer and a matching separate PyKotor checkout containing:

```text
Libraries/PyKotor/src/pykotor
Libraries/Utility/src/utility
Libraries/PyKotorGL/src/pykotor
```

Install the frontend dependencies in your Python environment:

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

`--backend-info` reports and checks the selected library paths without starting
the GUI. Dependency wheels and backend source are supplied separately.

## Native executable builds

The build follows HoloPatcher's fresh-PyInstaller pipeline. It uses the
`fresh-pyinstaller` action stored in your PyInstaller fork, validates its
`FRESH_PYINSTALLER_MANIFEST` against every native bootloader, and keeps UPX
disabled. Installing an upstream PyInstaller wheel is not part of this setup.
PyInstaller 6.x is required; CI uses Python 3.12.

| Platform | Distribution |
| --- | --- |
| Windows x64 | Single-file `.exe` |
| Linux x86-64 | AppImage containing an onedir payload, plus `.AppImage.tar.gz` |
| macOS Intel | Onedir `.app.zip` |
| macOS Apple Silicon | Onedir `.app.zip` |

### GitHub Actions

Run **Build optimized distributions with fresh PyInstaller** manually, or push
an existing release tag matching `v*`. The workflow resolves the backend and
PyInstaller refs to commits once, then uses those commits on every platform.
It uploads build artifacts and provenance; it does not publish a GitHub release.

The repository variables match HoloPatcher's setup:

| Variable | Default / purpose |
| --- | --- |
| `PYKOTOR_REPOSITORY` | `<repository-owner>/PyKotor` |
| `PYKOTOR_REF` | Backend ref; the manual `pykotor_ref` input overrides it |
| `PYINSTALLER_REPOSITORY` | `<repository-owner>/pyinstaller` |
| `PYINSTALLER_REF` | Fork ref; the manual `pyinstaller_ref` input overrides it |
| `SOURCE_READ_TOKEN` (optional secret) | Read access if either source repository is private |

The selected fork must include `.github/actions/fresh-pyinstaller/action.yml`.
The selected backend must include its compiler hook at
`Libraries/PyKotor/src/pykotor/__pyinstaller/hook-pykotor.resource.formats.ncs.compiler.py`.
This hook is required for source-checkout builds. The shared action compiles/installs
PyInstaller in the job's clean Python environment and supplies
`FRESH_PYINSTALLER_MANIFEST`. Its wheel and compilation manifest are also
uploaded so that the same native build can be installed locally.

### Local Windows build

Build on Windows with your backend checkout and the same Python environment
that received your fork's PyInstaller build. Install application dependencies
**before** running the fork's action/helper or installing its native wheel:

```powershell
python -m pip install -r requirements-build.txt
# Run the fork's fresh-pyinstaller helper, or install the wheel it produced.
$env:FRESH_PYINSTALLER_MANIFEST = "C:\build-tools\fresh-pyinstaller.json"
.\build_windows.ps1 -PyKotorRoot "C:\src\PyKotor"
```

Use the actual manifest path emitted by your fork. The helper produces
`dist/HolocronToolset.exe` and checks its embedded payload, compiler and startup.
The previous Windows OneDir mode is replaced by the single-file policy.

### Local release packages

With the same fork build installed, its manifest selected, and
`HOLOCRON_PYKOTOR_ROOT` set, run the matching native target:

```bash
python build/build_distributions.py --target windows-x64
python build/build_distributions.py --target linux-x86_64
python build/build_distributions.py --target macos-x86_64
python build/build_distributions.py --target macos-arm64
```

Each command runs on its target OS/architecture. Use an empty `release/`
directory. Output includes the distribution, SHA-256 checksums, dependency
versions, the fork manifest, and frontend/backend Git commits when available.
Source archives record `null` for unavailable Git commits.

Linux needs a compiler, zlib headers, `patchelf`, `desktop-file-validate`, and
Qt/OpenGL/XCB libraries; the workflow installs these and runs under Xvfb.
AppImage assembly uses the two URL/hash pins in `build/appimage-tools.json`.
macOS uses ad-hoc signing with the selected deployment target; these builds
are not notarized. No credentials for release signing are assumed.

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

Run the retained tests with the selected backend and development dependencies:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

Set `K1_PATH` and `K2_PATH` when testing against local game installations.
Qt/OpenGL interactions and executable builds require their platform dependencies.

Module Designer opens directly and edits existing modules through its 2D and
3D views. Generated UI and resource modules are committed because the
application imports them directly.

`src/toolset/data/instance_history.py` implements undo/redo for KotOR Game
Instance Table (GIT) records, shared by the GIT editor and Module Designer.

Automatic startup/help updates are disabled. The retained manual application
and help update actions still refer to the upstream project; use a release
source that matches this frontend and backend before relying on those actions.

See `LICENSE` for licensing.
