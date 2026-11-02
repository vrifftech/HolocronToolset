"""PyInstaller entry point; use the bundled backend rather than a source checkout."""
import multiprocessing
from pathlib import Path
import sys


def packaging_check(report: Path) -> int:
    """Check bundled backend/grammar/assets without a game or source checkout."""
    import contextlib
    import io
    import json
    import traceback

    try:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("Packaging checks must run from the frozen executable")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from toolset.bootstrap import backend_info
            info = backend_info()
            if info["root"] is not None:
                raise RuntimeError("Frozen application selected an external backend")
            from ply import yacc
            from pykotor.common.misc import Game
            from pykotor.resource.formats.ncs import bytes_ncs, compile_nss, read_ncs
            from toolset.data.script_analysis import analyze_script
            source = "void main() { int value = 1; }"
            for game in (Game.K1, Game.K2):
                if not read_ncs(bytes_ncs(compile_nss(source, game, errorlog=yacc.NullLogger()))).instructions:
                    raise RuntimeError(f"Empty compiled script for {game}")
                analysis = analyze_script(source, "packaging.nss", game.is_k2(), [], lambda *args: None)
                if analysis.error is not None:
                    raise RuntimeError(str(analysis.error))
            import toolset
            package = Path(toolset.__file__).parent
            if not (package / "help/contents.xml").is_file():
                raise RuntimeError("Bundled help is missing")
        result = {"ok": True, "frozen": True, "backend": info, "nss_compilation": "K1 and K2", "script_analysis": "passed"}
        status = 0
    except Exception:
        result, status = {"ok": False, "error": traceback.format_exc()}, 1
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return status

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-check":
        raise SystemExit(packaging_check(Path(sys.argv[2])))
    from toolset.__main__ import main
    raise SystemExit(main())
