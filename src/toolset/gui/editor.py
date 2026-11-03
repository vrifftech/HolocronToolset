from __future__ import annotations

import os

from abc import abstractmethod
from typing import TYPE_CHECKING, Callable

from pykotor.common.module import Module
from pykotor.common.stream import BinaryReader
from pykotor.extract.capsule import Capsule
from pykotor.extract.file import ResourceIdentifier
from pykotor.resource.formats.erf import ERFType, read_erf, write_erf
from pykotor.resource.formats.erf.erf_data import ERF
from pykotor.resource.formats.rim import read_rim, write_rim
from pykotor.resource.type import ResourceType
from pykotor.tools import module
from pykotor.tools.misc import is_any_erf_type_file, is_bif_file, is_capsule_file, is_rim_file
from pykotor.tools.path import CaseAwarePath
from PyQt5 import QtCore
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QAction, QFileDialog, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QShortcut, QWidget
from toolset.gui.dialogs.load_from_module import LoadFromModuleDialog
from toolset.gui.dialogs.save.to_bif import BifSaveDialog, BifSaveOption
from toolset.gui.dialogs.save.to_module import SaveToModuleDialog
from toolset.gui.dialogs.save.to_rim import RimSaveDialog, RimSaveOption
from toolset.gui.widgets.settings.installations import GlobalSettings
from utility.error_handling import assert_with_variable_trace, format_exception_with_variables, universal_simplify_exception
from utility.system.path import Path

if TYPE_CHECKING:
    from pykotor.common.language import LocalizedString
    from pykotor.resource.formats.rim.rim_data import RIM
    from toolset.data.installation import HTInstallation


class Editor(QMainWindow):
    """Editor is a base class for all file-specific editors.

    Provides methods for saving and loading files that are stored directly in folders and for files that are encapsulated in a MOD or RIM.
    """

    newFile = QtCore.pyqtSignal()
    savedFile = QtCore.pyqtSignal(object, object, object, object)
    childSavedFile = QtCore.pyqtSignal(object, object, object, object)
    loadedFile = QtCore.pyqtSignal(object, object, object, object)

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        iconName: str,
        readSupported: list[ResourceType],
        writeSupported: list[ResourceType],
        installation: HTInstallation | None = None,
        mainwindow: QMainWindow | None = None,
    ):
        """Initializes the editor.

        Args:
        ----
            parent: QWidget: The parent widget
            title: str: The title of the editor window
            iconName: str: The name of the icon to display
            readSupported: list[ResourceType]: The supported resource types for reading
            writeSupported: list[ResourceType]: The supported resource types for writing
            installation: HTInstallation | None: The installation context
            mainwindow: QMainWindow | None: The main window

        Initializes editor properties:
            - Sets up title, icon and parent widget
            - Sets supported read/write resource types
            - Initializes file filters for opening and saving
            - Sets up other editor properties.
        """
        super().__init__(parent)

        self._filepath: Path | None = None
        self._resname: str | None = None
        self._restype: ResourceType | None = None
        self._revert: bytes | None = None
        self._readSupported: list[ResourceType] = readSupported
        self._writeSupported: list[ResourceType] = writeSupported
        self._global_settings: GlobalSettings = GlobalSettings()
        self._installation: HTInstallation = installation
        self._mainwindow: QMainWindow | None = mainwindow
        self._resourceBrowser = None

        self._editorTitle: str = title
        self.setWindowTitle(title)
        self._setupIcon(iconName)

        capsule_types = " ".join(f"*.{e.name.lower()}" for e in ERFType) + " *.rim"
        self._saveFilter: str = "All valid files ("
        for resource in writeSupported:
            self._saveFilter += f'*.{resource.extension}{"" if writeSupported[-1] == resource else " "}'
        self._saveFilter += f" {capsule_types});;"
        for resource in writeSupported:
            self._saveFilter += f"{resource.category} File (*.{resource.extension});;"
        self._saveFilter += f"Save into module ({capsule_types})"

        self._openFilter: str = "All valid files ("
        for resource in readSupported:
            self._openFilter += f'*.{resource.extension}{"" if readSupported[-1] == resource else " "}'
        self._openFilter += f" {capsule_types});;"
        for resource in readSupported:
            self._openFilter += f"{resource.category} File (*.{resource.extension});;"
        self._openFilter += f"Load from module ({capsule_types})"

    def _setupMenus(self):
        """Sets up menu actions and keyboard shortcuts."""
        from toolset.gui.widgets.edit.resref import ResRefComboBox

        for field in self.findChildren(ResRefComboBox):
            field.bindResources(self._installation, lambda: self._filepath)
        self.savedFile.connect(self._invalidateResourceChoices)
        self.childSavedFile.connect(self._invalidateResourceChoices)
        for action in self.menuBar().actions()[0].menu().actions():
            if action.text() == "New":  # sourcery skip: extract-method
                action.triggered.connect(self.new)
            if action.text() == "Open":
                action.triggered.connect(self.open)
            if action.text() == "Save":
                action.triggered.connect(self.save)
            if action.text() == "Save As":
                action.triggered.connect(self.saveAs)
            if action.text() == "Revert":
                action.triggered.connect(self.revert)
            if action.text() == "Revert":
                action.setEnabled(False)
            if action.text() == "Exit":
                action.triggered.connect(self.close)  # type: ignore[]
        QShortcut("Ctrl+N", self).activated.connect(self.new)
        QShortcut("Ctrl+O", self).activated.connect(self.open)
        QShortcut("Ctrl+S", self).activated.connect(self.save)
        QShortcut("Ctrl+Shift+S", self).activated.connect(self.saveAs)
        QShortcut("Ctrl+R", self).activated.connect(self.revert)
        QShortcut("Ctrl+Q", self).activated.connect(self.exit)
        fileMenu = self.menuBar().actions()[0].menu()
        fileMenu.addSeparator()
        browse = QAction("Browse Resources…", self)
        browse.setShortcut("Ctrl+Shift+O")
        browse.triggered.connect(self.showResourceBrowser)
        fileMenu.addAction(browse)
        references = QAction("Find References…", self)
        references.setEnabled(self._installation is not None)
        references.triggered.connect(self.searchReferences)
        fileMenu.addAction(references)

    def searchReferences(self):
        from toolset.gui.dialogs.reference_search import ReferenceSearchDialog
        from toolset.utils.window import addWindow

        dialog = ReferenceSearchDialog(self, self._installation, self._filepath, self._resname or "")
        addWindow(dialog)

    def showResourceBrowser(self):
        from toolset.gui.widgets.resource_browser import ResourceBrowser

        if self._resourceBrowser is None:
            self._resourceBrowser = ResourceBrowser(self)
            self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._resourceBrowser)
        self._resourceBrowser.show()
        self._resourceBrowser.raise_()
        self._resourceBrowser.ensureResources()

    def resourceSaved(self, filepath, resname, restype, data):
        self.childSavedFile.emit(filepath, resname, restype, data)

    def _invalidateResourceChoices(self, *_):
        from toolset.gui.widgets.edit.resref import ResRefComboBox

        if self._installation is not None:
            self._installation.resource_catalog = None
        for field in self.findChildren(ResRefComboBox):
            field.invalidateResources()

    def _setupIcon(self, iconName: str):
        iconVersion = "x" if self._installation is None else "2" if self._installation.tsl else "1"
        iconPath = f":/images/icons/k{iconVersion}/{iconName}.png"
        self.setWindowIcon(QIcon(QPixmap(iconPath)))

    def refreshWindowTitle(self):
        """Refreshes the window title based on the current state."""
        installationName = "No Installation" if self._installation is None else self._installation.name  # TODO: Fix it always saying 'no installation' in every case.

        if self._filepath is None:
            self.setWindowTitle(self._editorTitle)
        elif is_capsule_file(self._filepath.name):
            self.setWindowTitle(f"{self._filepath.name}/{self._resname}.{self._restype.extension} - {installationName} - {self._editorTitle}")
        else:
            hierarchy: tuple[str, ...] = self._filepath.parts
            folder = f"{hierarchy[-2]}/" if len(hierarchy) >= 2 else ""
            self.setWindowTitle(f"{folder}{self._resname}.{self._restype.extension} - {installationName} - {self._editorTitle}")

    def saveAs(self) -> bool:
        """Select a destination; retain the current document identity if saving fails."""
        filepath_str, _filter = QFileDialog.getSaveFileName(self, "Save As", "", self._saveFilter, "")
        if not filepath_str:
            return False
        try:
            identifier = ResourceIdentifier.from_path(filepath_str).validate()
            if identifier.restype == ResourceType.SAV:
                raise ValueError("SAV archives are not supported.")
        except ValueError as error:
            QMessageBox(QMessageBox.Critical, "Invalid filename/extension", str(universal_simplify_exception(error))).exec_()
            return False

        resname, restype = identifier
        capsule_types = " ".join(f"*.{kind.name.lower()}" for kind in ERFType) + " *.rim"
        if is_capsule_file(filepath_str) and f"Save into module ({capsule_types})" in self._saveFilter:
            dialog = SaveToModuleDialog(self._resname or "new", self._restype or self._writeSupported[0], self._writeSupported)
            if not dialog.exec_():
                return False
            resname, restype = dialog.resname(), dialog.restype()

        previous = self._filepath, self._resname, self._restype, self._revert
        self._filepath, self._resname, self._restype = Path(filepath_str), resname, restype
        saved = False
        try:
            saved = self.save()
            return saved
        finally:
            if not saved:
                self._filepath, self._resname, self._restype, self._revert = previous
                self.refreshWindowTitle()

    def save(self) -> bool:
        """Write the document, returning False on cancellation or failure."""
        if self._filepath is None:
            return self.saveAs()

        previous = self._filepath, self._resname, self._restype, self._revert
        saved = False
        try:
            if not self._resolveSaveDestination():
                return False
            if self._restype == ResourceType.SAV or self._filepath.suffix.lower() == ".sav":
                raise ValueError("SAV archives are not supported.")
            data, data_ext = self.build()
            if data is None:
                return False
            if is_rim_file(self._filepath.name):
                saved = self._saveEndsWithRim(data, data_ext)
            elif is_any_erf_type_file(self._filepath.name):
                saved = self._saveEndsWithErf(data, data_ext)
            else:
                saved = self._saveEndsWithOther(data, data_ext)
            if saved:
                self.refreshWindowTitle()
                for action in self.menuBar().actions()[0].menu().actions():
                    if action.text() == "Revert":
                        action.setEnabled(True)
            return bool(saved)
        except Exception as error:
            details = format_exception_with_variables(error)
            try:
                with Path("errorlog.txt").open("a", encoding="utf-8") as log:
                    log.write(details + "\n----------------------\n")
            except OSError:
                print(details)
            QMessageBox(QMessageBox.Critical, "Failed to write to file", str(universal_simplify_exception(error))).exec_()
            return False
        finally:
            if not saved:
                self._filepath, self._resname, self._restype, self._revert = previous

    def _resolveSaveDestination(self) -> bool:
        """Resolve read-only BIF and disabled-RIM destinations before serialization."""
        if is_bif_file(self._filepath.name):
            dialog = BifSaveDialog(self)
            dialog.exec_()
            if dialog.option == BifSaveOption.MOD:
                filepath, _filter = QFileDialog.getSaveFileName(self, "Save As", "", ".MOD File (*.mod)", "")
                if not filepath:
                    return False
                if not is_any_erf_type_file(filepath) or Path(filepath).suffix.lower() == ".sav":
                    raise ValueError("Choose a writable ERF/MOD archive for this resource.")
                resource = SaveToModuleDialog(self._resname, self._restype, self._writeSupported)
                if not resource.exec_():
                    return False
                self._filepath = Path(filepath)
                self._resname, self._restype = resource.resname(), resource.restype()
            elif dialog.option == BifSaveOption.Override:
                self._filepath = self._installation.override_path() / f"{self._resname}.{self._restype.extension}"
            else:
                return False
        if is_rim_file(self._filepath.name) and self._global_settings.disableRIMSaving:
            dialog = RimSaveDialog(self)
            dialog.exec_()
            if dialog.option == RimSaveOption.MOD:
                self._filepath = self._filepath.parent / f"{Module.find_root(self._filepath)}.mod"
            elif dialog.option == RimSaveOption.Override:
                self._filepath = self._installation.override_path() / f"{self._resname}.{self._restype.extension}"
            else:
                return False
        return True

    def _saveEndsWithRim(self, data: bytes, data_ext: bytes) -> bool:
        """Write a resource to the already resolved RIM destination."""
        rim = read_rim(self._filepath)
        if self._restype == ResourceType.MDL:
            rim.set_data(self._resname, ResourceType.MDX, data_ext)
        rim.set_data(self._resname, self._restype, data)
        write_rim(rim, self._filepath)
        self._revert = data
        self.savedFile.emit(str(self._filepath), self._resname, self._restype, data)
        if self._restype == ResourceType.MDL:
            self.savedFile.emit(str(self._filepath), self._resname, ResourceType.MDX, data_ext)
        if self._installation is not None:
            self._reloadSavedModule(self._filepath.name)
        return True

    def _saveEndsWithErf(self, data: bytes, data_ext: bytes) -> bool:
        # Create the mod file if it does not exist.
        """Saves data to an ERF/MOD file with the given extension."""
        assert self._filepath is not None, assert_with_variable_trace(self._filepath is not None)
        assert self._resname is not None, assert_with_variable_trace(self._resname is not None)
        assert self._restype is not None, assert_with_variable_trace(self._restype is not None)

        erftype: ERFType = ERFType.from_extension(self._filepath)
        c_filepath: CaseAwarePath = CaseAwarePath.pathify(self._filepath)
        if c_filepath.is_file():
            erf: ERF = read_erf(c_filepath)
        elif c_filepath.with_suffix(".rim").is_file():
            module.rim_to_mod(c_filepath)
            erf = read_erf(c_filepath)
        else:  # originally in a bif, user chose to save into erf/mod.
            print(f"Saving '{self._resname}.{self._restype}' to a blank new {erftype.name} file at {c_filepath}")
            erf = ERF(erftype)  # create a new ERF I guess.
        erf.erf_type = erftype

        # MDL is a special case - we need to save the MDX file with the MDL file.
        if self._restype == ResourceType.MDL:
            assert data_ext is not None, assert_with_variable_trace(data_ext is not None)
            erf.set_data(self._resname, ResourceType.MDX, data_ext)

        erf.set_data(self._resname, self._restype, data)

        write_erf(erf, c_filepath)
        self._revert = data
        self.savedFile.emit(str(c_filepath), self._resname, self._restype, data)
        if self._restype == ResourceType.MDL:
            self.savedFile.emit(str(c_filepath), self._resname, ResourceType.MDX, data_ext)

        # Update installation cache
        if self._installation is not None and c_filepath.parent == self._installation.module_path():
            self._reloadSavedModule(c_filepath.name)
        return True

    def _saveEndsWithOther(self, data: bytes, data_ext: bytes) -> bool:
        assert self._filepath is not None, assert_with_variable_trace(self._filepath is not None)

        c_filepath: CaseAwarePath = CaseAwarePath.pathify(self._filepath)
        if self._restype == ResourceType.MDL:
            # Commit the associated resources together, after validating both buffers.
            from pykotor.resource.formats.mdl import read_mdl, write_mdl

            model = read_mdl(data, source_ext=data_ext)
            write_mdl(model, c_filepath)
        else:
            with c_filepath.open("wb") as file:
                file.write(data)

        self._revert = data
        self.savedFile.emit(self._filepath, self._resname, self._restype, data)
        return True

    def _reloadSavedModule(self, filename):
        """Report a cache refresh failure without treating a completed write as failed."""
        try:
            self._installation.reload_module(filename)
        except Exception as error:
            QMessageBox.warning(self, "File saved; refresh failed", str(universal_simplify_exception(error)))

    def open(self):    # noqa: A003
        """Opens a file dialog to select a file to open."""
        filepath_str, filter = QFileDialog.getOpenFileName(self, "Open file", "", self._openFilter)
        if not filepath_str.strip():
            return
        r_filepath = Path(filepath_str)
        if r_filepath.suffix.lower() == ".sav":
            QMessageBox(QMessageBox.Critical, "Unsupported file format", "SAV archives are not supported.", QMessageBox.Ok, self).exec_()
            return

        capsule_types = " ".join(f"*.{e.name.lower()}" for e in ERFType) + " *.rim"
        if is_capsule_file(r_filepath) and f"Load from module ({capsule_types})" in self._openFilter:
            dialog = LoadFromModuleDialog(Capsule(r_filepath), self._readSupported)
            if dialog.exec_():
                self.load_module_from_dialog_info(dialog, r_filepath)
        else:
            data: bytes = BinaryReader.load_file(r_filepath)
            res_ident: ResourceIdentifier = ResourceIdentifier.from_path(r_filepath).validate()
            self.load(r_filepath, *res_ident, data)

    def load_module_from_dialog_info(self, dialog: LoadFromModuleDialog, c_filepath: Path):
        resname: str | None = dialog.resname()
        restype: ResourceType | None = dialog.restype()
        data: bytes | None = dialog.data()
        assert resname is not None, assert_with_variable_trace(resname is not None)
        assert restype is not None, assert_with_variable_trace(resname is not None)
        assert data is not None, assert_with_variable_trace(resname is not None)

        self.load(c_filepath, resname, restype, data)

    @abstractmethod
    def build(self) -> tuple[bytes, bytes]:
        ...

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        """Load a resource from a file."""
        if restype == ResourceType.SAV or Path.pathify(filepath).suffix.lower() == ".sav":
            raise ValueError("SAV archives are not supported.")
        self._filepath = Path.pathify(filepath)  # type: ignore[reportGeneralTypeIssues]
        self._resname = resref
        self._restype = restype
        self._revert = data
        for action in self.menuBar().actions()[0].menu().actions():
            if action.text() == "Revert":
                action.setEnabled(True)
        self.refreshWindowTitle()
        self.loadedFile.emit(str(self._filepath), self._resname, self._restype, data)

    def exit(self):  # noqa: A003
        self.close()

    def new(self):
        self._revert = None
        self._filepath = None
        for action in self.menuBar().actions()[0].menu().actions():
            if action.text() == "Revert":
                action.setEnabled(False)
        self.refreshWindowTitle()
        self.newFile.emit()

    def revert(self):
        if self._revert is not None:
            assert self._filepath is not None, assert_with_variable_trace(self._filepath is not None)
            assert self._resname is not None, assert_with_variable_trace(self._resname is not None)
            assert self._restype is not None, assert_with_variable_trace(self._restype is not None)
            self.load(self._filepath, self._resname, self._restype, self._revert)

    def _loadLocstring(self, textbox: QLineEdit | QPlainTextEdit, locstring: LocalizedString):
        """Loads a LocalizedString into a textbox."""
        setText: Callable[..., None] = textbox.setPlainText if isinstance(textbox, QPlainTextEdit) else textbox.setText
        className = "QLineEdit" if isinstance(textbox, QLineEdit) else "QPlainTextEdit"

        textbox.locstring = locstring
        if locstring.stringref == -1:
            text = str(locstring)
            setText(text if text != "-1" else "")
            textbox.setStyleSheet(className + " {background-color: white;}")
        else:
            setText(self._installation.string(locstring))
            textbox.setStyleSheet(className + " {background-color: #fffded;}")

    def filepath(self) -> str | None:
        return None if self._filepath is None else str(self._filepath)
