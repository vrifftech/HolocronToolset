"""Launch Holocron Toolset or one of its editors with the selected backend."""
from __future__ import annotations

import argparse
import importlib
import json
import multiprocessing
import os
import sys
from pathlib import Path
from toolset.bootstrap import backend_info


EDITORS = {
    "are": "AREEditor", "bwm": "BWMEditor", "dlg": "DLGEditor", "erf": "ERFEditor",
    "gff": "GFFEditor", "git": "GITEditor", "ifo": "IFOEditor", "jrl": "JRLEditor",
    "mdl": "MDLEditor", "nss": "NSSEditor", "pth": "PTHEditor", "ssf": "SSFEditor",
    "tlk": "TLKEditor", "tpc": "TPCEditor", "twoda": "TwoDAEditor", "txt": "TXTEditor",
    "utc": "UTCEditor", "utd": "UTDEditor", "ute": "UTEEditor", "uti": "UTIEditor",
    "utm": "UTMEditor", "utp": "UTPEditor", "uts": "UTSEditor", "utt": "UTTEditor",
    "utw": "UTWEditor",
}
INSTALLATION_EDITORS = {
    "are", "dlg", "git", "ifo", "jrl", "nss",
    "utc", "utd", "ute", "uti", "utm", "utp", "uts", "utt", "utw",
}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def onAppCrash(etype, error, traceback):
    from utility.error_handling import format_exception_with_variables
    with Path("errorlog.txt").open("a", encoding="utf-8") as log:
        log.write(format_exception_with_variables(error, etype, traceback))
    sys.__excepthook__(etype, error, traceback)


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Holocron Toolset")
    parser.add_argument("--backend-info", action="store_true", help="Print the selected backend paths without starting Qt")
    parser.add_argument("--editor", choices=sorted(EDITORS), help="Launch only the selected editor")
    parser.add_argument("--file", type=Path, help="Open a file in the selected editor; omit to create a new document")
    game = parser.add_mutually_exclusive_group()
    game.add_argument("--k1", type=Path, metavar="DIRECTORY", help="KotOR game directory for the standalone editor")
    game.add_argument("--k2", type=Path, metavar="DIRECTORY", help="TSL game directory for the standalone editor")
    args, qt_args = parser.parse_known_args()
    if args.backend_info:
        print(json.dumps(backend_info(), indent=2))
        return 0
    if (args.file is not None or args.k1 is not None or args.k2 is not None) and args.editor is None:
        parser.error("--file, --k1 and --k2 require --editor")
    game_path = args.k1 if args.k1 is not None else args.k2
    if args.editor in INSTALLATION_EDITORS and game_path is None:
        parser.error(f"--editor {args.editor} requires --k1 DIRECTORY or --k2 DIRECTORY")
    file_path = args.file.expanduser().resolve() if args.file is not None else None
    game_path = game_path.expanduser().resolve() if game_path is not None else None
    from PyQt5.QtCore import QThread, Qt
    from PyQt5.QtWidgets import QApplication
    # The help browser uses paths relative to the application package.
    os.chdir(Path(__file__).resolve().parent)
    if os.name == "nt":
        os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")
    # Qt owns widget/event scaling. Only custom OpenGL reads use device pixels.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0], *qt_args])
    app.thread().setPriority(QThread.HighestPriority)
    sys.excepthook = onAppCrash
    import toolset.resources_rc

    if args.editor is None:
        from toolset.gui.windows.main import ToolWindow
        window = ToolWindow()
    else:
        installation = None
        if game_path is not None:
            from toolset.data.installation import HTInstallation
            from toolset.gui.dialogs.asyncloader import AsyncLoader

            tsl = args.k2 is not None

            def load_installation(report):
                result = HTInstallation(str(game_path), game_path.name, tsl, None, progress_callback=report)
                result.progress_callback = None
                return result

            loader = AsyncLoader(None, "Loading installation", load_installation, "Unable to load installation", with_progress=True)
            if not loader.exec_():
                return 1
            installation = loader.value
            if installation.game().is_k2() != tsl:
                parser.error("The selected game directory does not match --k1/--k2")
        module = importlib.import_module(f"toolset.gui.editors.{args.editor}")
        window = getattr(module, EDITORS[args.editor])(None, installation)
        if file_path is not None:
            from pykotor.extract.file import ResourceIdentifier

            identifier = ResourceIdentifier.from_path(file_path).validate()
            if identifier.restype not in window._readSupported:
                parser.error(f"The {args.editor} editor does not support {identifier.restype.name} files")
            window.load(file_path, identifier.resname, identifier.restype, file_path.read_bytes())
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
