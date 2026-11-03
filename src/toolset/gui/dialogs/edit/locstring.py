from copy import deepcopy

from pykotor.common.language import Gender, Language, LocalizedString
from pykotor.resource.formats.tlk import read_tlk, write_tlk
from pykotor.tools.path import CaseAwarePath
from PyQt5.QtWidgets import QDialog, QWidget
from toolset.data.installation import HTInstallation


class LocalizedStringDialog(QDialog):
    def __init__(self, parent: QWidget, installation: HTInstallation, locstring: LocalizedString):
        super().__init__(parent)

        from toolset.uic.dialogs.locstring import Ui_Dialog

        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        self.ui.stringrefSpin.setRange(-1, 0xFFFFFFFE)
        table_language = installation.talktable().language()
        language_name = table_language.name.title() if isinstance(table_language, Language) else f"Language {table_language}"
        self.setWindowTitle(f"{language_name} - {installation.name} - Localized String Editor")
        self.ui.languageSelect.clear()
        for language in Language:
            if language.get_encoding() is not None:
                self.ui.languageSelect.addItem(language.name.title(), int(language))
        for language, _, _ in locstring:
            if self.ui.languageSelect.findData(int(language)) == -1:
                self.ui.languageSelect.addItem(f"Language {language}", int(language))
        if locstring._substrings:
            language, gender = LocalizedString.substring_pair(next(iter(locstring._substrings)))
            self.ui.languageSelect.setCurrentIndex(self.ui.languageSelect.findData(int(language)))
            self.ui.femaleRadio.setChecked(gender is Gender.FEMALE)
            self.ui.maleRadio.setChecked(gender is Gender.MALE)
        self.ui.textSourceCombo.setCurrentIndex(int(locstring.stringref != -1 and not locstring._substrings))

        self.ui.textSourceCombo.currentIndexChanged.connect(self.sourceChanged)
        self.ui.stringrefSpin.valueChanged.connect(self.stringrefChanged)
        self.ui.stringrefNewButton.clicked.connect(self.newTlkString)
        self.ui.stringrefNoneButton.clicked.connect(self.noTlkString)
        self.ui.maleRadio.clicked.connect(self.substringChanged)
        self.ui.femaleRadio.clicked.connect(self.substringChanged)
        self.ui.languageSelect.currentIndexChanged.connect(self.substringChanged)
        self.ui.stringEdit.textChanged.connect(self.stringEdited)

        self._installation = installation
        self.locstring = deepcopy(locstring)
        self.ui.stringrefSpin.setValue(locstring.stringref)
        self.stringrefChanged(locstring.stringref)

    def accept(self):
        if self.ui.textSourceCombo.currentIndex() == 1:
            index = self.locstring.stringref & 0xFFFFFF
            tlk_path = CaseAwarePath(self._installation.path(), "dialog.tlk")
            tlk = read_tlk(tlk_path)
            created = len(tlk) <= index
            if created:
                tlk.resize(index + 1)
            entry = tlk.get(index)
            text = self.ui.stringEdit.toPlainText()
            if created or self._tlk_text != text:
                entry.replace(text=text)
                write_tlk(tlk, tlk_path)
        super().accept()

    def reject(self):
        super().reject()

    def stringrefChanged(self, stringref: int):
        self.locstring.stringref = stringref
        self.ui.textSourceCombo.model().item(1).setEnabled(stringref != -1)
        if stringref == -1:
            self.ui.textSourceCombo.setCurrentIndex(0)
        self.sourceChanged()

    def sourceChanged(self):
        embedded = self.ui.textSourceCombo.currentIndex() == 0
        self.ui.substringFrame.setVisible(embedded)
        if embedded:
            self._update_text()
        else:
            self.ui.stringEdit.setPlainText(self._installation.talktable().string(self.locstring.stringref))
            self._tlk_text = self.ui.stringEdit.toPlainText()

    def newTlkString(self):
        self.ui.stringrefSpin.setValue(self._installation.talktable().size())
        self.ui.textSourceCombo.setCurrentIndex(1)

    def noTlkString(self):
        self.ui.stringrefSpin.setValue(-1)

    def substringChanged(self):
        self._update_text()

    def _update_text(self):
        language = self.ui.languageSelect.currentData()
        gender = Gender(int(self.ui.femaleRadio.isChecked()))
        text = (
            self.locstring.get(language, gender)
            if self.locstring.get(language, gender) is not None
            else ""
        )
        previous = self.ui.stringEdit.blockSignals(True)
        self.ui.stringEdit.setPlainText(text)
        self.ui.stringEdit.blockSignals(previous)

    def stringEdited(self):
        if self.ui.textSourceCombo.currentIndex() == 0:
            language = self.ui.languageSelect.currentData()
            gender = Gender(int(self.ui.femaleRadio.isChecked()))
            self.locstring.set_data(language, gender, self.ui.stringEdit.toPlainText())
