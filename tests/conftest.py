"""Use the standalone Toolset and selected backend in the retained GUI tests."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from toolset.bootstrap import bootstrap_backend
bootstrap_backend()
