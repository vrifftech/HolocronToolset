param(
    [Parameter(Mandatory=$true)][string]$PyKotorRoot,
    [ValidateSet("OneFile")][string]$Mode = "OneFile",
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Build a Windows executable on Windows." }
$env:HOLOCRON_PYKOTOR_ROOT = (Resolve-Path -LiteralPath $PyKotorRoot).Path
$env:FRONTEND_BUILD_MODE = $Mode.ToLowerInvariant()
$root = $PSScriptRoot
if (-not $env:FRESH_PYINSTALLER_MANIFEST -or -not (Test-Path -LiteralPath $env:FRESH_PYINSTALLER_MANIFEST -PathType Leaf)) {
    throw "Run your PyInstaller fork's fresh-pyinstaller action/helper first and set FRESH_PYINSTALLER_MANIFEST. See README.md."
}
$env:FRESH_PYINSTALLER_MANIFEST = (Resolve-Path -LiteralPath $env:FRESH_PYINSTALLER_MANIFEST).Path
Push-Location $root
try {
    & $Python -c "import importlib; [importlib.import_module(n) for n in ['PyQt5.QtWidgets', 'PyQt5.QtMultimedia', 'OpenGL.GL', 'glm', 'numpy', 'PIL', 'ply', 'markdown', 'watchdog', 'jsmin', 'pyperclip', 'PyInstaller']]"
    if ($LASTEXITCODE -ne 0) { throw "Install requirements-build.txt and your freshly built PyInstaller in this Python environment. See README.md." }
    & $Python "$root\run.py" --backend-info
    if ($LASTEXITCODE -ne 0) { throw "Backend validation failed." }
    & $Python -m PyInstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\.pyinstaller-build" "$root\build\HolocronToolset.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    $exe = Join-Path $root "dist\HolocronToolset.exe"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Expected executable not found: $exe" }
    & $Python -c "import sys; from pathlib import Path; sys.path.insert(0, 'build'); from build_policy import verify_onefile_payload; from smoke_test import smoke_test; executable = Path(sys.argv[1]); print(verify_onefile_payload(executable)); smoke_test(executable)" $exe
    if ($LASTEXITCODE -ne 0) { throw "Frozen payload or startup verification failed." }
    Get-FileHash -LiteralPath $exe -Algorithm SHA256
} finally { Pop-Location }
