from PyQt5.QtCore import QRegularExpression
from PyQt5.QtGui import QRegularExpressionValidator
from PyQt5.QtWidgets import QLineEdit, QWidget


class FloatEdit(QLineEdit):
    """Edit a float's round-trip representation without decimal-place rounding."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        expression = QRegularExpression(
            r"[+-]?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|(?i:inf(?:inity)?|nan))"
        )
        self.setValidator(QRegularExpressionValidator(expression, self))
        self.setValue(0.0)
        self.editingFinished.connect(self._commit)

    def setValue(self, value: float):
        self._value = float(value)
        self.setText(repr(self._value))

    def _commit(self):
        # Unedited NaNs keep their original payload and sign as well.
        text = self.text()
        self._value = self._value if text == repr(self._value) else float(text)

    def value(self) -> float:
        return self._value
