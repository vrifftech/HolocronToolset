from __future__ import annotations

import re

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QShortcut, QVBoxLayout, QWidget,
)


class FindReplaceDialog(QDialog):
    findRequested = pyqtSignal(bool)
    replaceRequested = pyqtSignal()
    replaceAllRequested = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("Find and Replace")
        self.findEdit = QLineEdit(self)
        self.replaceEdit = QLineEdit(self)
        self.caseSensitive = QCheckBox("Match case", self)
        self.wholeWords = QCheckBox("Whole words", self)
        self.status = QLabel(self)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Find:", self.findEdit)
        form.addRow("Replace with:", self.replaceEdit)
        layout.addLayout(form)
        options = QHBoxLayout()
        options.addWidget(self.caseSensitive)
        options.addWidget(self.wholeWords)
        layout.addLayout(options)
        buttons = QHBoxLayout()
        previous = QPushButton("Previous", self)
        following = QPushButton("Next", self)
        replace = QPushButton("Replace", self)
        replace_all = QPushButton("Replace All", self)
        close = QPushButton("Close", self)
        self.searchButtons = (previous, following, replace, replace_all)
        for button in (*self.searchButtons, close):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        previous.clicked.connect(lambda: self.findRequested.emit(True))
        following.clicked.connect(lambda: self.findRequested.emit(False))
        replace.clicked.connect(lambda: self.replaceRequested.emit())
        replace_all.clicked.connect(lambda: self.replaceAllRequested.emit())
        close.clicked.connect(self.hide)
        self.findEdit.returnPressed.connect(lambda: self.findRequested.emit(False))
        QShortcut("F3", self).activated.connect(lambda: self.findRequested.emit(False))
        QShortcut("Shift+F3", self).activated.connect(lambda: self.findRequested.emit(True))
        self.findEdit.textChanged.connect(self._updateButtons)
        self._updateButtons("")

    def _updateButtons(self, text):
        for button in self.searchButtons:
            button.setEnabled(bool(text))

    def openFor(self, *, replace: bool = False, selection: str = ""):
        if selection and "\u2029" not in selection and "\n" not in selection:
            self.findEdit.setText(selection)
        self.status.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        field = self.replaceEdit if replace else self.findEdit
        field.setFocus()
        field.selectAll()

    def expression(self) -> re.Pattern[str]:
        pattern = re.escape(self.findEdit.text())
        if self.wholeWords.isChecked():
            pattern = rf"(?<!\w){pattern}(?!\w)"
        return re.compile(pattern, 0 if self.caseSensitive.isChecked() else re.IGNORECASE)
