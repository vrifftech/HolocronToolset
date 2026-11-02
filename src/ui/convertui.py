"""Regenerate the retained PyQt5 UI/resource modules from this checkout."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    for source in sorted((ROOT / "ui").rglob("*.ui")):
        target = ROOT / "toolset/uic" / source.relative_to(ROOT / "ui").with_suffix(".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "PyQt5.uic.pyuic", "--import-from=toolset", str(source), "-o", str(target)], check=True)
    subprocess.run([sys.executable, "-m", "PyQt5.pyrcc_main", str(ROOT / "resources/resources.qrc"), "-o", str(ROOT / "toolset/resources_rc.py")], check=True)

if __name__ == "__main__":
    main()
