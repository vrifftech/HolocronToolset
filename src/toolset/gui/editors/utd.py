from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from pykotor.common.misc import ResRef
from pykotor.common.stream import BinaryWriter
from pykotor.resource.formats.gff import write_gff
from pykotor.resource.generics.dlg import DLG, dismantle_dlg
from pykotor.resource.generics.utd import UTD, dismantle_utd, read_utd
from pykotor.resource.type import ResourceType
from pykotor.tools import door
from PyQt5.QtWidgets import QMessageBox, QWidget
from toolset.data.installation import HTInstallation
from toolset.gui.editor import Editor
from toolset.gui.widgets.settings.installations import GlobalSettings
from toolset.utils.window import openResourceEditor

if TYPE_CHECKING:
    import os

    from pykotor.extract.file import ResourceResult
    from pykotor.resource.formats.twoda.twoda_data import TwoDA


class UTDEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: HTInstallation | None = None, *, mainwindow=None):
        """Initialize the Door Editor."""
        supported: list[ResourceType] = [ResourceType.UTD]
        super().__init__(parent, "Door Editor", "door", supported, supported, installation, mainwindow)

        self.globalSettings: GlobalSettings = GlobalSettings()
        self._genericdoors2DA: TwoDA = installation.htGetCache2DA("genericdoors")
        self._utd: UTD = UTD()

        from toolset.uic.editors.utd import Ui_MainWindow
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupMenus()
        self._setupSignals()
        self._setupInstallation(installation)

        self.update3dPreview()
        self.new()

    def _setupSignals(self):
        """Connect GUI buttons and signals to methods."""
        self.ui.tagGenerateButton.clicked.connect(self.generateTag)
        self.ui.resrefGenerateButton.clicked.connect(self.generateResref)
        self.ui.conversationModifyButton.clicked.connect(self.editConversation)

        self.ui.appearanceSelect.currentIndexChanged.connect(self.update3dPreview)
        self.ui.actionShowPreview.triggered.connect(self.togglePreview)

    def _setupInstallation(self, installation: HTInstallation):
        """Sets up the installation for editing."""
        self._installation = installation
        self.ui.nameEdit.setInstallation(installation)
        self.ui.previewRenderer.installation = installation

        # Load required 2da files if they have not been loaded already
        required: list[str] = [HTInstallation.TwoDA_DOORS, HTInstallation.TwoDA_FACTIONS]
        installation.htBatchCache2DA(required)

        appearances: TwoDA = installation.htGetCache2DA(HTInstallation.TwoDA_DOORS)
        factions: TwoDA = installation.htGetCache2DA(HTInstallation.TwoDA_FACTIONS)

        self.ui.appearanceSelect.setItems(appearances.get_column("label"))
        self.ui.factionSelect.setItems(factions.get_column("label"))

        self.ui.notBlastableCheckbox.setVisible(installation.tsl)
        self.ui.difficultyModSpin.setVisible(installation.tsl)
        self.ui.difficultySpin.setVisible(installation.tsl)
        self.ui.difficultyLabel.setVisible(installation.tsl)
        self.ui.difficultyModLabel.setVisible(installation.tsl)

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        super().load(filepath, resref, restype, data)

        utd = read_utd(data)
        self._loadUTD(utd)

    def _loadUTD(self, utd: UTD):
        """Loads UTD data into UI elements."""
        self._utd = utd

        # Basic
        self.ui.nameEdit.setLocstring(utd.name)
        self.ui.tagEdit.setText(utd.tag)
        self.ui.resrefEdit.setText(str(utd.resref))
        self.ui.appearanceSelect.setCurrentIndex(utd.appearance_id)
        self.ui.conversationEdit.setText(str(utd.conversation))

        # Advanced
        self.ui.min1HpCheckbox.setChecked(utd.min1_hp)
        self.ui.plotCheckbox.setChecked(utd.plot)
        self.ui.staticCheckbox.setChecked(utd.static)
        self.ui.notBlastableCheckbox.setChecked(utd.not_blastable)
        self.ui.factionSelect.setCurrentIndex(utd.faction_id)
        self.ui.animationState.setValue(utd.animation_state)
        self.ui.currenHpSpin.setValue(utd.current_hp)
        self.ui.maxHpSpin.setValue(utd.maximum_hp)
        self.ui.hardnessSpin.setValue(utd.hardness)
        self.ui.fortitudeSpin.setValue(utd.fortitude)
        self.ui.reflexSpin.setValue(utd.reflex)
        self.ui.willSpin.setValue(utd.willpower)

        # Lock
        self.ui.needKeyCheckbox.setChecked(utd.key_required)
        self.ui.removeKeyCheckbox.setChecked(utd.auto_remove_key)
        self.ui.keyEdit.setText(utd.key_name)
        self.ui.lockedCheckbox.setChecked(utd.locked)
        self.ui.openLockSpin.setValue(utd.unlock_dc)
        self.ui.difficultySpin.setValue(utd.unlock_diff)
        self.ui.difficultyModSpin.setValue(utd.unlock_diff_mod)

        # Scripts
        self.ui.onClickEdit.setText(str(utd.on_click))
        self.ui.onClosedEdit.setText(str(utd.on_closed))
        self.ui.onDamagedEdit.setText(str(utd.on_damaged))
        self.ui.onDeathEdit.setText(str(utd.on_death))
        self.ui.onOpenFailedEdit.setText(str(utd.on_open_failed))
        self.ui.onHeartbeatEdit.setText(str(utd.on_heartbeat))
        self.ui.onMeleeAttackEdit.setText(str(utd.on_melee))
        self.ui.onSpellEdit.setText(str(utd.on_power))
        self.ui.onOpenEdit.setText(str(utd.on_open))
        self.ui.onUnlockEdit.setText(str(utd.on_unlock))
        self.ui.onUserDefinedEdit.setText(str(utd.on_user_defined))

        # Comments
        self.ui.commentsEdit.setPlainText(utd.comment)

    def build(self) -> tuple[bytes, bytes]:
        """Builds a UTD object from UI data."""
        utd: UTD = deepcopy(self._utd)

        # Basic
        utd.name = self.ui.nameEdit.locstring()
        utd.tag = self.ui.tagEdit.text()
        if str(utd.resref) != self.ui.resrefEdit.text():
            utd.resref = ResRef(self.ui.resrefEdit.text())
        utd.appearance_id = self.ui.appearanceSelect.currentIndex()
        if str(utd.conversation) != self.ui.conversationEdit.text():
            utd.conversation = ResRef(self.ui.conversationEdit.text())

        # Advanced
        utd.min1_hp = self.ui.min1HpCheckbox.isChecked()
        utd.plot = self.ui.plotCheckbox.isChecked()
        utd.static = self.ui.staticCheckbox.isChecked()
        utd.not_blastable = self.ui.notBlastableCheckbox.isChecked()
        utd.faction_id = self.ui.factionSelect.currentIndex()
        utd.animation_state = self.ui.animationState.value()
        utd.current_hp = self.ui.currenHpSpin.value()
        utd.maximum_hp = self.ui.maxHpSpin.value()
        utd.hardness = self.ui.hardnessSpin.value()
        utd.fortitude = self.ui.fortitudeSpin.value()
        utd.reflex = self.ui.reflexSpin.value()
        utd.willpower = self.ui.willSpin.value()

        # Lock
        utd.locked = self.ui.lockedCheckbox.isChecked()
        utd.unlock_dc = self.ui.openLockSpin.value()
        utd.unlock_diff = self.ui.difficultySpin.value()
        utd.unlock_diff_mod = self.ui.difficultyModSpin.value()
        utd.key_required = self.ui.needKeyCheckbox.isChecked()
        utd.auto_remove_key = self.ui.removeKeyCheckbox.isChecked()
        utd.key_name = self.ui.keyEdit.text()

        # Scripts
        if str(utd.on_click) != self.ui.onClickEdit.text():
            utd.on_click = ResRef(self.ui.onClickEdit.text())
        if str(utd.on_closed) != self.ui.onClosedEdit.text():
            utd.on_closed = ResRef(self.ui.onClosedEdit.text())
        if str(utd.on_damaged) != self.ui.onDamagedEdit.text():
            utd.on_damaged = ResRef(self.ui.onDamagedEdit.text())
        if str(utd.on_death) != self.ui.onDeathEdit.text():
            utd.on_death = ResRef(self.ui.onDeathEdit.text())
        if str(utd.on_open_failed) != self.ui.onOpenFailedEdit.text():
            utd.on_open_failed = ResRef(self.ui.onOpenFailedEdit.text())
        if str(utd.on_heartbeat) != self.ui.onHeartbeatEdit.text():
            utd.on_heartbeat = ResRef(self.ui.onHeartbeatEdit.text())
        if str(utd.on_melee) != self.ui.onMeleeAttackEdit.text():
            utd.on_melee = ResRef(self.ui.onMeleeAttackEdit.text())
        if str(utd.on_power) != self.ui.onSpellEdit.text():
            utd.on_power = ResRef(self.ui.onSpellEdit.text())
        if str(utd.on_open) != self.ui.onOpenEdit.text():
            utd.on_open = ResRef(self.ui.onOpenEdit.text())
        if str(utd.on_unlock) != self.ui.onUnlockEdit.text():
            utd.on_unlock = ResRef(self.ui.onUnlockEdit.text())
        if str(utd.on_user_defined) != self.ui.onUserDefinedEdit.text():
            utd.on_user_defined = ResRef(self.ui.onUserDefinedEdit.text())

        # Comments
        utd.comment = self.ui.commentsEdit.toPlainText()

        data = bytearray()
        gff = dismantle_utd(utd)
        write_gff(gff, data)

        return data, b""

    def new(self):
        super().new()
        self._loadUTD(UTD())


    def generateTag(self):
        if self.ui.resrefEdit.text() == "":
            self.generateResref()
        self.ui.tagEdit.setText(self.ui.resrefEdit.text())

    def generateResref(self):
        if self._resname is not None and self._resname != "":
            self.ui.resrefEdit.setText(self._resname)
        else:
            self.ui.resrefEdit.setText("m00xx_dor_000")

    def editConversation(self):
        """Edits a conversation."""
        resname = self.ui.conversationEdit.text()
        data, filepath = None, None

        if resname == "":
            QMessageBox(QMessageBox.Critical, "Failed to open DLG Editor", "Conversation field cannot be blank.").exec_()
            return

        search = self._installation.resource(resname, ResourceType.DLG)

        if search is None:
            msgbox = QMessageBox(QMessageBox.Information, "DLG file not found",
                                 "Do you wish to create a file in the override?",
                                 QMessageBox.Yes | QMessageBox.No).exec_()
            if QMessageBox.Yes == msgbox:
                data = bytearray()

                write_gff(dismantle_dlg(DLG()), data)
                filepath = self._installation.override_path() / f"{resname}.dlg"
                writer = BinaryWriter.to_file(filepath)
                writer.write_bytes(data)
                writer.close()
        else:
            resname, restype, filepath, data = search

        if data is not None:
            openResourceEditor(filepath, resname, ResourceType.DLG, data, self._installation, self)

    def togglePreview(self):
        self.globalSettings.showPreviewUTP = not self.globalSettings.showPreviewUTP
        self.update3dPreview()

    def update3dPreview(self):
        """Updates the 3D preview renderer visibility and size."""
        self.ui.previewRenderer.setVisible(self.globalSettings.showPreviewUTP)
        self.ui.actionShowPreview.setChecked(self.globalSettings.showPreviewUTP)

        if self.globalSettings.showPreviewUTP:
            self._update_model()
        else:
            self.setFixedSize(374, 457)

    def _update_model(self):
        """Updates the model preview."""
        self.setFixedSize(674, 457)

        data, _ = self.build()
        modelname: str = door.get_model(read_utd(data), self._installation, genericdoors=self._genericdoors2DA)
        mdl: ResourceResult | None = self._installation.resource(modelname, ResourceType.MDL)
        mdx: ResourceResult | None = self._installation.resource(modelname, ResourceType.MDX)
        if mdl is not None and mdx is not None:
            self.ui.previewRenderer.setModel(mdl.data, mdx.data)
        else:
            self.ui.previewRenderer.clearModel()
