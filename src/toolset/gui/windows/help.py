from __future__ import annotations

import xml.etree.ElementTree as ElemTree
from typing import TYPE_CHECKING, Callable

import markdown
from pykotor.common.stream import BinaryReader
from pykotor.tools.encoding import decode_bytes_with_fallbacks
from PyQt5 import QtCore
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QTreeWidgetItem, QWidget
from utility.error_handling import universal_simplify_exception
from utility.system.path import Path

if TYPE_CHECKING:
    import os


class HelpWindow(QMainWindow):
    def __init__(self, parent: QWidget | None, startingPage: str | None = None):
        super().__init__(parent)

        from toolset.uic.windows import help
        self.ui = help.Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupSignals()
        self._setupContents()

        self.ui.textDisplay.setSearchPaths(["./help"])

        if startingPage:
            self.displayFile(startingPage)

    def _setupSignals(self):
        self.ui.contentsTree.clicked.connect(self.onContentsClicked)

    def _setupContents(self):
        self.ui.contentsTree.clear()

        try:
            tree = ElemTree.parse("./help/contents.xml")
            root = tree.getroot()

            self._setupContentsRecXML(None, root)

        except Exception as e:
            print(f"Suppressed error: {universal_simplify_exception(e)}")

    def _setupContentsRecXML(self, parent: QTreeWidgetItem | None, element: ElemTree.Element):
        add: Callable[..., None] = self.ui.contentsTree.addTopLevelItem if parent is None else parent.addChild

        for child in element:
            item = QTreeWidgetItem([child.get("name")])  # FIXME: typing
            item.setData(0, QtCore.Qt.UserRole, child.get("file"))  # type: ignore[attr-defined]
            add(item)
            self._setupContentsRecXML(item, child)

    def displayFile(self, filepath: os.PathLike | str):
        filepath = Path.pathify(filepath)
        try:
            text: str = decode_bytes_with_fallbacks(BinaryReader.load_file(filepath))
            html: str = markdown.markdown(text, extensions=["tables", "fenced_code", "codehilite"]) if filepath.suffix.lower() == ".md" else text
            self.ui.textDisplay.setHtml(html)
        except OSError as e:
            QMessageBox(
                QMessageBox.Critical,
                "Failed to open help file",
                f"Could not access '{filepath}'.\n{universal_simplify_exception(e)}",
            ).exec_()

    def onContentsClicked(self):
        if self.ui.contentsTree.selectedItems():
            item: QTreeWidgetItem = self.ui.contentsTree.selectedItems()[0]
            filename = item.data(0, QtCore.Qt.UserRole)  # type: ignore[attr-defined]
            if filename:
                help_path = Path("./help").resolve()
                file_path = Path(help_path, filename)
                self.ui.textDisplay.setSearchPaths([str(help_path), str(file_path.parent)])
                self.displayFile(file_path)
