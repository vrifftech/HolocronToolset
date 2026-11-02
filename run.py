"""Run HolocronToolset from this checkout without installing it."""
from pathlib import Path
import runpy
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    runpy.run_module("toolset", run_name="__main__")
