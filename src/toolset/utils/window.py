from __future__ import annotations

from typing import TYPE_CHECKING

from pykotor.resource.formats.erf.erf_data import ERFType
from pykotor.resource.type import ResourceType
from PyQt5.QtWidgets import QMessageBox, QWidget
from toolset.gui.widgets.settings.installations import GlobalSettings
from utility.error_handling import assert_with_variable_trace, universal_simplify_exception
from utility.system.path import PurePath

if TYPE_CHECKING:
    import os

    from toolset.gui.editor import Editor
    from toolset.data.installation import HTInstallation

windows: list[QWidget] = []


def addWindow(window: QWidget):
    close_event = window.closeEvent

    def removeFromList(a0):
        close_event(a0)
        if a0.isAccepted() and window in windows:
            windows.remove(window)

    windows.append(window)
    window.closeEvent = removeFromList
    window.show()


def openResourceEditor(
    filepath: os.PathLike | str,
    resref: str,
    restype: ResourceType,
    data: bytes,
    installation: HTInstallation | None = None,
    parentwindow: QWidget | None = None,
    gff_specialized: bool | None = None,
) -> tuple[os.PathLike | str, Editor] | tuple[None, None]:
    """Open a resource with only its selected editor's dependencies.

    Return its path and window, or (None, None) for an unsupported format.
    """
    if restype == ResourceType.SAV or PurePath.pathify(filepath).suffix.lower() == ".sav":
        QMessageBox(QMessageBox.Critical, "Unsupported file format", "SAV archives are not supported.", QMessageBox.Ok, parentwindow).show()
        return None, None

    if gff_specialized is None:
        gff_specialized = GlobalSettings().gff_specializedEditors

    editor = None

    if restype in [ResourceType.TwoDA, ResourceType.TwoDA_CSV, ResourceType.TwoDA_JSON]:
        from toolset.gui.editors.twoda import TwoDAEditor
        editor = TwoDAEditor(None, installation)

    elif restype in [ResourceType.SSF, ResourceType.SSF_XML]:
        from toolset.gui.editors.ssf import SSFEditor
        editor = SSFEditor(None, installation)

    elif restype in [ResourceType.TLK, ResourceType.TLK_XML, ResourceType.TLK_JSON]:
        from toolset.gui.editors.tlk import TLKEditor
        editor = TLKEditor(None, installation)

    elif restype in [ResourceType.WOK, ResourceType.DWK, ResourceType.PWK]:
        from toolset.gui.editors.bwm import BWMEditor
        editor = BWMEditor(None, installation)

    elif restype in [ResourceType.TPC, ResourceType.TGA, ResourceType.JPG, ResourceType.BMP, ResourceType.PNG]:
        from toolset.gui.editors.tpc import TPCEditor
        editor = TPCEditor(None, installation)

    elif restype in [ResourceType.TXT, ResourceType.TXI, ResourceType.LYT, ResourceType.VIS]:
        from toolset.gui.editors.txt import TXTEditor
        editor = TXTEditor(None, installation)

    elif restype in [ResourceType.NSS]:
        if installation:  # noqa: SIM108
            from toolset.gui.editors.nss import NSSEditor
            editor = NSSEditor(None, installation)
        else:
            from toolset.gui.editors.txt import TXTEditor
            editor = TXTEditor(None, installation)

    elif restype in [ResourceType.NCS] and installation:
        from toolset.gui.editors.nss import NSSEditor
        editor = NSSEditor(None, installation)

    elif restype in [ResourceType.DLG, ResourceType.DLG_XML]:
        if installation is None:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.dlg import DLGEditor
            editor = DLGEditor(None, installation)

    elif restype in [ResourceType.UTC, ResourceType.UTC_XML]:
        if installation is None or not gff_specialized:
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.utc import UTCEditor
            editor = UTCEditor(None, installation, mainwindow=parentwindow)

    elif restype in [ResourceType.UTP, ResourceType.UTP_XML]:
        if installation is None or not gff_specialized:
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.utp import UTPEditor
            editor = UTPEditor(None, installation, mainwindow=parentwindow)

    elif restype in [ResourceType.UTD, ResourceType.UTD_XML]:
        if installation is None or not gff_specialized:
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.utd import UTDEditor
            editor = UTDEditor(None, installation, mainwindow=parentwindow)

    elif restype in [ResourceType.UTS, ResourceType.UTS_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.uts import UTSEditor
            editor = UTSEditor(None, installation)

    elif restype in [ResourceType.UTT, ResourceType.UTT_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.utt import UTTEditor
            editor = UTTEditor(None, installation)

    elif restype in [ResourceType.UTM, ResourceType.UTM_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.utm import UTMEditor
            editor = UTMEditor(None, installation)

    elif restype in [ResourceType.UTW, ResourceType.UTW_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.utw import UTWEditor
            editor = UTWEditor(None, installation)

    elif restype in [ResourceType.UTE, ResourceType.UTE_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.ute import UTEEditor
            editor = UTEEditor(None, installation)

    elif restype in [ResourceType.UTI, ResourceType.UTI_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.uti import UTIEditor
            editor = UTIEditor(None, installation)

    elif restype in [ResourceType.JRL, ResourceType.JRL_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.jrl import JRLEditor
            editor = JRLEditor(None, installation)

    elif restype in [ResourceType.ARE, ResourceType.ARE_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.are import AREEditor
            editor = AREEditor(None, installation)

    elif restype in [ResourceType.PTH, ResourceType.PTH_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.pth import PTHEditor
            editor = PTHEditor(None, installation)

    elif restype in [ResourceType.GIT, ResourceType.GIT_XML]:
        if installation is None or not gff_specialized:  # noqa: SIM108
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.git import GITEditor
            editor = GITEditor(None, installation)

    elif restype in [ResourceType.IFO, ResourceType.IFO_XML]:
        if installation is None or not gff_specialized:
            from toolset.gui.editors.gff import GFFEditor
            editor = GFFEditor(None, installation)
        else:
            from toolset.gui.editors.ifo import IFOEditor
            editor = IFOEditor(None, installation)

    elif restype in [
        ResourceType.GFF,
        ResourceType.GFF_XML,
        ResourceType.ITP,
        ResourceType.ITP_XML,
        ResourceType.GUI,
        ResourceType.GUI_XML,
        ResourceType.RES,
        ResourceType.RES_XML,
        ResourceType.FAC,
        ResourceType.FAC_XML,
    ]:
        from toolset.gui.editors.gff import GFFEditor
        editor = GFFEditor(None, installation)

    elif restype in [ResourceType.WAV, ResourceType.MP3]:
        assert parentwindow is not None, assert_with_variable_trace(parentwindow is not None)
        from toolset.gui.windows.audio_player import AudioPlayer
        editor = AudioPlayer(parentwindow)

    elif restype.name in ERFType.__members__ or restype == ResourceType.RIM:
        from toolset.gui.editors.erf import ERFEditor
        editor = ERFEditor(None, installation)

    elif restype in [ResourceType.MDL, ResourceType.MDX]:
        from toolset.gui.editors.mdl import MDLEditor
        editor = MDLEditor(None, installation)

    if editor is not None:
        try:
            editor.load(filepath, resref, restype, data)
            addWindow(editor)

        except Exception as e:
            QMessageBox(
                QMessageBox.Critical,
                "An unexpected error has occurred",
                str(universal_simplify_exception(e)),
                QMessageBox.Ok,
                parentwindow
            ).show()
            raise
        else:
            return filepath, editor  # type: ignore[]
    else:
        QMessageBox(
            QMessageBox.Critical,
            "Failed to open file",
            f"The selected file format '{restype}' is not yet supported.",
            QMessageBox.Ok,
            parentwindow,
        ).show()

    return None, None
