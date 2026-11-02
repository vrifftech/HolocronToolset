from __future__ import annotations

from typing import TYPE_CHECKING

from pykotor.resource.type import ResourceType
from PyQt5 import QtCore
from PyQt5.QtWidgets import QDialog, QListWidgetItem, QWidget
from toolset.gui.dialogs.asyncloader import AsyncLoader
from toolset.utils.window import openResourceEditor

if TYPE_CHECKING:
    from pykotor.extract.file import FileResource
    from toolset.data.installation import HTInstallation


class FileSearcher(QDialog):

    def __init__(self, parent: QWidget, installations: dict[str, HTInstallation]):
        super().__init__(parent)

        from toolset.uic.dialogs import search
        self.ui = search.Ui_Dialog()
        self.ui.setupUi(self)

        self.results: list[FileResource] = []
        self.installation: HTInstallation | None = None

        self._installations: dict[str, HTInstallation] = installations
        for name, installation in installations.items():
            self.ui.installationSelect.addItem(name, installation)

    def accept(self):
        """Submits search parameters and starts search.

        Implicit Args:
        ----
            installation: {Installation object selected by user}
            caseSensitive: {True if case sensitive search is checked, False otherwise}
            filenamesOnly: {True if filenames only search is checked, False otherwise}
            text: {Search text entered by user}
            searchCore: {True if core search is checked, False otherwise}
            searchModules: {True if modules search is checked, False otherwise}
            searchOverride: {True if override search is checked, False otherwise}
            checkTypes: {List of selected resource types}.

        Returns
        -------
            None
        {Processes user search parameters by:
            - Getting selected installation
            - Getting search options from UI
            - Mapping checked resource types to list
            - Calling search function to start search}.
        """
        installation: HTInstallation = self.ui.installationSelect.currentData()
        caseSensitive = self.ui.caseSensitiveRadio.isChecked()
        filenamesOnly = self.ui.filenamesOnlyCheck.isChecked()
        text = self.ui.searchTextEdit.text()

        searchCore = self.ui.coreCheck.isChecked()
        searchModules = self.ui.modulesCheck.isChecked()
        searchOverride = self.ui.overrideCheck.isChecked()

        checkTypes: list[ResourceType] = []
        if self.ui.typeARECheck.isChecked(): checkTypes.append(ResourceType.ARE)  # noqa: E701
        if self.ui.typeGITCheck.isChecked(): checkTypes.append(ResourceType.GIT)  # noqa: E701
        if self.ui.typeIFOCheck.isChecked(): checkTypes.append(ResourceType.IFO)  # noqa: E701
        if self.ui.typeDLGCheck.isChecked(): checkTypes.append(ResourceType.DLG)  # noqa: E701
        if self.ui.typeJRLCheck.isChecked(): checkTypes.append(ResourceType.JRL)  # noqa: E701
        if self.ui.typeUTCCheck.isChecked(): checkTypes.append(ResourceType.UTC)  # noqa: E701
        if self.ui.typeUTDCheck.isChecked(): checkTypes.append(ResourceType.UTD)  # noqa: E701
        if self.ui.typeUTECheck.isChecked(): checkTypes.append(ResourceType.UTE)  # noqa: E701
        if self.ui.typeUTICheck.isChecked(): checkTypes.append(ResourceType.UTI)  # noqa: E701
        if self.ui.typeUTPCheck.isChecked(): checkTypes.append(ResourceType.UTP)  # noqa: E701
        if self.ui.typeUTMCheck.isChecked(): checkTypes.append(ResourceType.UTM)  # noqa: E701
        if self.ui.typeUTWCheck.isChecked(): checkTypes.append(ResourceType.UTW)  # noqa: E701
        if self.ui.typeUTSCheck.isChecked(): checkTypes.append(ResourceType.UTS)  # noqa: E701
        if self.ui.typeUTTCheck.isChecked(): checkTypes.append(ResourceType.UTT)  # noqa: E701
        if self.ui.type2DACheck.isChecked(): checkTypes.append(ResourceType.TwoDA)  # noqa: E701
        if self.ui.typeNSSCheck.isChecked(): checkTypes.append(ResourceType.NSS)  # noqa: E701
        if self.ui.typeNCSCheck.isChecked(): checkTypes.append(ResourceType.NCS)  # noqa: E701

        if self.search(installation, caseSensitive, filenamesOnly, text, searchCore, searchModules, searchOverride, checkTypes):
            self.installation = installation
            super().accept()

    def search(self, installation: HTInstallation, caseSensitive: bool, filenamesOnly: bool, text: str,
               searchCore: bool, searchModules: bool, searchOverride: bool, checkTypes: list[ResourceType]) -> bool:
        """Enumerate and search on the worker; publish results only after completion."""
        self.results = []
        selected_types = frozenset(checkTypes)
        lowercase_text = text.lower()

        def resources(report):
            if searchCore:
                yield from installation.chitin_resources()
            if searchModules:
                for module in installation.modules_list():
                    report(f"Searching module {module}", "update_subtask_text")
                    yield from installation.module_resources(module)
            if searchOverride:
                for folder in installation.override_list():
                    report(f"Searching Override folder {folder}", "update_subtask_text")
                    yield from installation.override_resources(folder)

        def collect(report):
            report(0, "set_maximum")
            results = []
            if not selected_types:
                return results
            for index, resource in enumerate(resources(report)):
                if index % 64 == 0:
                    report(f"Checking resource {index + 1}: {resource.resname()}", "update_subtask_text")
                if resource.restype() not in selected_types:
                    continue
                name = resource.resname()
                name_match = text in name if caseSensitive else lowercase_text in name.lower()
                if name_match:
                    results.append(resource)
                elif not filenamesOnly:
                    report(f"Reading {name}", "update_subtask_text")
                    data = resource.data().decode("utf-8", errors="ignore")
                    data_match = text in data if caseSensitive else lowercase_text in data.lower()
                    if data_match:
                        results.append(resource)
            return results

        loader = AsyncLoader(self, "Searching...", collect, "Search failed", with_progress=True)
        if not loader.exec_():
            return False
        self.results = loader.value
        return True


class FileResults(QDialog):
    def __init__(self, parent: QWidget, results: list[FileResource], installation: HTInstallation):
        """Initialize the search results dialog."""
        super().__init__(parent)

        from toolset.uic.dialogs.search_result import Ui_Dialog
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)

        self.ui.openButton.clicked.connect(self.open)
        self.ui.okButton.clicked.connect(self.accept)

        self.selection: FileResource | None = None
        self.installation: HTInstallation = installation

        for result in results:
            item = QListWidgetItem(result.filename())
            item.setData(QtCore.Qt.UserRole, result)
            item.setToolTip(str(result.filepath()))
            self.ui.resultList.addItem(item)

        self.ui.resultList.sortItems(QtCore.Qt.AscendingOrder)

    def accept(self):
        """Accepts the current selection from the result list.

        Args:
        ----
            self: The object instance.

        Returns:
        -------
            None: Does not return anything.

        Processes the current selection:
            - Gets the current item from the result list
            - Gets the data associated with the item if it exists
            - Sets the selection attribute to the data
            - Calls the parent accept method.
        """
        item = self.ui.resultList.currentItem()
        self.selection = item.data(QtCore.Qt.UserRole) if item is not None else None
        super().accept()

    def open(self):  # noqa: A003
        """Opens the current item in the result list."""
        item: QListWidgetItem | None = self.ui.resultList.currentItem()
        if item:
            resource: FileResource = item.data(QtCore.Qt.UserRole)
            openResourceEditor(resource.filepath(), resource.resname(), resource.restype(), resource.data(),
                               self.installation, self.window().parent())
