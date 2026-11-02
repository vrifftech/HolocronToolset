from __future__ import annotations

from PyQt5 import QtCore
from PyQt5.QtWidgets import QDockWidget, QLabel, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from pykotor.common.scriptdefs import KOTOR_CONSTANTS, KOTOR_FUNCTIONS, TSL_CONSTANTS, TSL_FUNCTIONS


class ScriptAnalysisDock(QDockWidget):
    navigate = QtCore.pyqtSignal(object, object)

    def __init__(self, editor):
        super().__init__("Script analysis", editor)
        self.setObjectName("scriptAnalysis")
        self._result = None
        self._tsl = editor._installation.tsl
        body = QWidget(self)
        self.setWidget(body)
        layout = QVBoxLayout(body)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(("Kind", "Location", "Declaration / message"))
        self.details = QPlainTextEdit(self)
        self.details.setFont(editor.ui.codeEdit.font())
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(130)
        self.openButton = QPushButton("Go to source location", self)
        for widget in (self.status, self.tree, self.details, self.openButton):
            layout.addWidget(widget)
        self.tree.itemSelectionChanged.connect(self._selectionChanged)
        self.tree.itemActivated.connect(lambda _item, _column: self._navigate())
        self.openButton.clicked.connect(self._navigate)
        editor.loadedFile.connect(self.clear)
        editor.newFile.connect(self.clear)
        self.clear()

    def clear(self, *_):
        self.tree.clear()
        self._result = None
        self.details.clear()
        self.status.setText("Run diagnostics with Ctrl+F5, or look up a top-level definition with F12.")
        self.openButton.setEnabled(False)

    def _add(self, kind, location, text, details):
        item = QTreeWidgetItem((kind, str(location) if location is not None else "", text))
        item.setData(0, QtCore.Qt.UserRole, location)
        item.setData(0, QtCore.Qt.UserRole + 1, details)
        self.tree.addTopLevelItem(item)

    def setAnalysis(self, result, symbol=None):
        self.clear()
        self._result = result
        if result.error is not None:
            error = result.error
            self._add("Error", error.location, error.message, str(error))
            for location in error.include_stack:
                self._add("Included from", location, location.line_text, location.excerpt())
            self.details.setPlainText(str(error))
        if symbol is None:
            self.status.setText("Analysis stopped at the first compiler error." if result.error is not None else
                                "Built-in compiler syntax and semantic checks passed. F5 also checks entry points and emits compiled code.")
        else:
            matches = [definition for definition in result.definitions if definition.name == symbol]
            if any(definition.kind == "Function" for definition in matches):
                matches = [definition for definition in matches if definition.kind != "Prototype"]
            for definition in matches:
                self._add(definition.kind, definition.location, definition.name, definition.location.excerpt())
            for function in (TSL_FUNCTIONS if self._tsl else KOTOR_FUNCTIONS):
                if function.name == symbol:
                    self._add("Built-in function", None, str(function), function.description + "\n" + str(function))
            for constant in (TSL_CONSTANTS if self._tsl else KOTOR_CONSTANTS):
                if constant.name == symbol:
                    self._add("Built-in constant", None, str(constant), str(constant))
            self.status.setText(f"Top-level declarations named {symbol!r}. Local variable bindings are not resolved.")
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        self._selectionChanged()

    def _selectionChanged(self):
        items = self.tree.selectedItems()
        location = items[0].data(0, QtCore.Qt.UserRole) if items else None
        self.openButton.setEnabled(location is not None and location.filename in self._result.sources)
        if items:
            self.details.setPlainText(items[0].data(0, QtCore.Qt.UserRole + 1))

    def _navigate(self):
        if self.openButton.isEnabled():
            location = self.tree.selectedItems()[0].data(0, QtCore.Qt.UserRole)
            self.navigate.emit(location, self._result.sources[location.filename])
