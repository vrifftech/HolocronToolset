from PyQt5 import QtCore
from PyQt5.QtWidgets import QAbstractSpinBox


class LongSpinBox(QAbstractSpinBox):
    """Bounded integer editing, including signed and unsigned 64-bit values."""

    valueChanged = QtCore.pyqtSignal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self._min = 0
        self._max = 0xFFFFFFFF
        self._value = 0
        self.lineEdit().setText("0")
        self.lineEdit().textEdited.connect(self._textEdited)
        # Register before consumers so editingFinished observers see the committed value.
        self.editingFinished.connect(self.clampLineEdit)

    def stepUp(self):
        self.stepBy(1)

    def stepDown(self):
        self.stepBy(-1)

    def stepBy(self, steps: int):
        self.clampLineEdit()
        self.setValue(self._value + steps)

    def text(self) -> str:
        return self.lineEdit().text()

    def setRange(self, min_value: int, max_value: int):
        if min_value > max_value:
            raise ValueError("The minimum cannot exceed the maximum.")
        self._min, self._max = min_value, max_value
        self.setValue(self._value)

    def _commitValue(self, value):
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)

    def _textEdited(self, text):
        try:
            value = int(text)
        except ValueError:
            return
        if self._min <= value <= self._max:
            self._commitValue(value)

    def clampLineEdit(self):
        try:
            value = int(self.lineEdit().text())
        except ValueError:
            value = self._value
        self.setValue(value)

    def setValue(self, value: int):
        value = value if isinstance(value, int) else 0
        value = max(self._min, min(self._max, value))
        self.lineEdit().setText(str(value))
        self._commitValue(value)

    def value(self) -> int:
        return self._value

    def stepEnabled(self) -> QAbstractSpinBox.StepEnabled:
        flags = self.StepNone
        if self._value < self._max:
            flags |= self.StepUpEnabled
        if self._value > self._min:
            flags |= self.StepDownEnabled
        return flags
