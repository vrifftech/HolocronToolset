from __future__ import annotations

import math
import struct
from typing import TYPE_CHECKING, Any

from pykotor.common.geometry import Vector3, Vector4
from pykotor.common.language import Gender, Language, LocalizedString
from pykotor.common.misc import ResRef
from pykotor.extract.talktable import TalkTable
from pykotor.resource.formats.gff import GFF, GFFContent, GFFFieldType, GFFList, GFFStruct, read_gff, write_gff
from pykotor.resource.type import ResourceType
from PyQt5 import QtCore
from PyQt5.QtCore import QItemSelectionRange, QModelIndex, QSortFilterProxyModel
from PyQt5.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import QFileDialog, QListWidgetItem, QMenu, QMessageBox, QShortcut, QWidget
from toolset.gui.editor import Editor

if TYPE_CHECKING:
    import os

    from pykotor.extract.installation import Installation

_VALUE_NODE_ROLE = QtCore.Qt.UserRole + 1
_RAW_NODE_ROLE = QtCore.Qt.UserRole + 10
_ORIGINAL_NODE_ROLE = QtCore.Qt.UserRole + 11
_STRUCT_BINARY_ROLE = QtCore.Qt.UserRole + 12
_FIELD_BINARY_ROLE = QtCore.Qt.UserRole + 13
_SOURCE_KEY_ROLE = QtCore.Qt.UserRole + 14
_TYPE_NODE_ROLE = QtCore.Qt.UserRole + 2
_LABEL_NODE_ROLE = QtCore.Qt.UserRole + 3

_ID_SUBSTRING_ROLE = QtCore.Qt.UserRole + 1
_TEXT_SUBSTRING_ROLE = QtCore.Qt.UserRole + 2


_INTEGER_RANGES = {
    GFFFieldType.UInt8: (0, 0xFF),
    GFFFieldType.UInt16: (0, 0xFFFF),
    GFFFieldType.UInt32: (0, 0xFFFFFFFF),
    GFFFieldType.UInt64: (0, 0xFFFFFFFFFFFFFFFF),
    GFFFieldType.Int8: (-0x80, 0x7F),
    GFFFieldType.Int16: (-0x8000, 0x7FFF),
    GFFFieldType.Int32: (-0x80000000, 0x7FFFFFFF),
    GFFFieldType.Int64: (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF),
}


class GFFEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: Installation | None = None):
        supported: list[ResourceType] = [
            ResourceType.GFF,
            ResourceType.UTC,
            ResourceType.UTP,
            ResourceType.UTD,
            ResourceType.UTI,
            ResourceType.UTM,
            ResourceType.UTE,
            ResourceType.UTT,
            ResourceType.UTW,
            ResourceType.UTS,
            ResourceType.DLG,
            ResourceType.GUI,
            ResourceType.ARE,
            ResourceType.IFO,
            ResourceType.GIT,
            ResourceType.JRL,
            ResourceType.ITP,
        ]
        super().__init__(parent, "GFF Editor", "none", supported, supported, installation)
        self.resize(400, 250)

        self._talktable: TalkTable | None = installation.talktable() if installation else None

        from toolset.uic.editors.gff import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.ui.stringrefSpin.setRange(-1, 0xFFFFFFFE)
        self.ui.substringLangCombo.clear()
        for language in Language:
            if language.get_encoding() is not None:
                self.ui.substringLangCombo.addItem(language.name.title(), int(language))
        self._setupMenus()
        self._setupSignals()

        self.ui.treeView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        self.ui.treeView.sortByColumn(0, QtCore.Qt.AscendingOrder)
        self.ui.treeView.setSortingEnabled(True)

        # Make the right panel take as little space possible
        self.ui.splitter.setSizes([99999999, 1])

        self.new()

    def _setupSignals(self):
        """Sets up signals and connections for the GUI.

        Args:
        ----
            self: The class instance.

        Processing Logic:
        ----------------
            - Connects actions to methods like selecting talk tables
            - Sets up models and views for the tree widget
            - Connects editing signals from widgets to updateData()
            - Connects context menu requests
            - Connects type combo box changes.
        """
        self.ui.actionSetTLK.triggered.connect(self.selectTalkTable)

        self.model: QStandardItemModel = QStandardItemModel(self)
        self.proxyModel: QSortFilterProxyModel = GFFSortFilterProxyModel(self)
        self.proxyModel.setSourceModel(self.model)
        self.ui.treeView.setModel(self.proxyModel)

        self.ui.treeView.selectionModel().selectionChanged.connect(self.selectionChanged)
        self.ui.intSpin.editingFinished.connect(self.updateData)
        self.ui.floatSpin.editingFinished.connect(self.updateData)
        self.ui.lineEdit.editingFinished.connect(self.updateData)
        self.ui.textEdit.textChanged.connect(self.updateData)
        self.ui.xVec3Spin.editingFinished.connect(self.updateData)
        self.ui.yVec3Spin.editingFinished.connect(self.updateData)
        self.ui.zVec3Spin.editingFinished.connect(self.updateData)
        self.ui.xVec4Spin.editingFinished.connect(self.updateData)
        self.ui.yVec4Spin.editingFinished.connect(self.updateData)
        self.ui.zVec4Spin.editingFinished.connect(self.updateData)
        self.ui.wVec4Spin.editingFinished.connect(self.updateData)
        self.ui.labelEdit.editingFinished.connect(self.updateData)

        self.ui.stringrefSpin.valueChanged.connect(self.changeLocstringText)
        self.ui.stringrefSpin.editingFinished.connect(self.updateData)
        self.ui.substringList.itemSelectionChanged.connect(self.substringSelected)
        self.ui.addSubstringButton.clicked.connect(self.addSubstring)
        self.ui.removeSubstringButton.clicked.connect(self.removeSubstring)
        self.ui.substringEdit.textChanged.connect(self.substringEdited)

        self.ui.treeView.customContextMenuRequested.connect(self.requestContextMenu)

        self.ui.typeCombo.activated.connect(self.typeChanged)

        QShortcut("Del", self).activated.connect(self.removeSelectedNodes)

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        """Loads resource data from a file into the model.

        Args:
        ----
            filepath: {Path or filename to load from}
            resref: {Unique identifier for resource}
            restype: {Type of resource}
            data: {Resource data}.

        Processing Logic:
        ----------------
            - Reads GFF data structure from loaded bytes
            - Clears existing model data
            - Sets model column count to 1
            - Adds root node to model
            - Recursively loads GFF structure into model from root node
            - Expands root node in tree view after loading.
        """
        super().load(filepath, resref, restype, data)
        gff: GFF = read_gff(data)
        self._content = gff.content if gff._binary_data else None
        self._binary_data = gff._binary_data

        self.model.clear()
        self.model.setColumnCount(1)

        rootNode = QStandardItem("[ROOT]")
        rootNode.setForeground(QBrush(QColor(0x660000)))
        self.model.appendRow(rootNode)
        rootNode.setData(gff.root.struct_id, _VALUE_NODE_ROLE)
        self._load_struct(rootNode, gff.root)

        sourceIndex = self.model.indexFromItem(rootNode)
        proxyIndex = self.proxyModel.mapFromSource(sourceIndex)
        self.ui.treeView.expand(proxyIndex)

    def _load_struct(self, node: QStandardItem, gffStruct: GFFStruct):
        """Loads a GFFStruct into a QStandardItem node.

        Args:
        ----
            node: QStandardItem - The parent node to load the struct into
            gffStruct: GFFStruct - The struct to load

        Processing Logic:
        ----------------
            - Loops through each field in the struct
            - Creates a child node for each field
            - Sets the node type, label and value
            - Handles list and struct field types recursively
            - Appends the child node to the parent node.
        """
        node.setData(gffStruct._binary, _STRUCT_BINARY_ROLE)
        node.setData(gffStruct._source_key, _SOURCE_KEY_ROLE)
        for label, field in gffStruct._field_order:
            ftype, value = field.field_type(), field.value()
            childNode = QStandardItem("")
            childNode.setData(ftype, _TYPE_NODE_ROLE)
            childNode.setData(label, _LABEL_NODE_ROLE)
            childNode.setData(field._binary, _FIELD_BINARY_ROLE)
            if ftype is GFFFieldType.String:
                childNode.setData(field.raw_data, _RAW_NODE_ROLE)
                childNode.setData(value, _ORIGINAL_NODE_ROLE)

            if ftype == GFFFieldType.List:
                childNode.setForeground(QBrush(QColor(0x000088)))
                self._load_list(childNode, value)
            elif ftype == GFFFieldType.Struct:
                childNode.setForeground(QBrush(QColor(0x660000)))
                childNode.setData(value.struct_id, _VALUE_NODE_ROLE)
                self._load_struct(childNode, value)
            else:
                childNode.setData(value, _VALUE_NODE_ROLE)

            self.refreshItemText(childNode)
            node.appendRow(childNode)

    def _load_list(self, node: QStandardItem, gffList: GFFList):
        """Load GFF data into a tree view.

        Args:
        ----
            node: QStandardItem - Parent node to add children
            gffList: GFFList - List of GFF structures to load

        Processing Logic:
        ----------------
            - Loop through each GFF structure in the list
            - Create a child node for each structure
            - Set the node text and color
            - Add the node to the parent node
            - Load any child data for the structure into the node.
        """
        for gffStruct in gffList:
            childNode = QStandardItem("")
            childNode.setForeground(QBrush(QColor(0x660000)))
            childNode.setData(gffStruct.struct_id, _VALUE_NODE_ROLE)
            node.appendRow(childNode)
            self.refreshItemText(childNode)
            self._load_struct(childNode, gffStruct)

    def build(self) -> tuple[bytes, bytes]:
        """Builds a GFF file from the model.

        Args:
        ----
            self: The object calling the function.

        Returns:
        -------
            bytes: The built GFF file
            bytes: An empty byte array

        Processing Logic:
        ----------------
            - Creates a GFFContent object to hold the GFF data
            - Initializes a GFF object with the GFFContent
            - Calls _build_struct to populate the GFF structure from the model
            - Writes the populated GFF to a byte array
            - Returns the byte array and an empty byte array.
        """
        content = self._content
        if content is None:
            content = GFFContent(f"{self._restype.target_type().extension.upper()} ") if self._restype else GFFContent.GFF
        gff = GFF(content)
        gff._binary_data = self._binary_data

        self._build_struct(self.model.item(0, 0), gff.root)

        data = bytearray()
        file_format = ResourceType.GFF_XML if self._restype and self._restype.extension.endswith(".xml") else ResourceType.GFF
        write_gff(gff, data, file_format)
        return data, b""

    def _build_struct(self, item: QStandardItem, gffStruct: GFFStruct):
        """Builds a GFF structure from a QStandardItem model.

        Args:
        ----
            item: QStandardItem - Item containing node data
            gffStruct: GFFStruct - Structure to populate.

        Processing Logic:
        ----------------
            - Loops through each child node of the item
            - Gets label, value, and type from node data
            - Calls corresponding set method on gffStruct based on type
            - Recursively builds child structures and lists.
        """
        struct_id = item.data(_VALUE_NODE_ROLE)
        gffStruct._binary = item.data(_STRUCT_BINARY_ROLE)
        gffStruct._source_key = item.data(_SOURCE_KEY_ROLE)
        if struct_id is not None:
            gffStruct.struct_id = struct_id
        for i in range(item.rowCount()):
            child = item.child(i, 0)
            label = child.data(_LABEL_NODE_ROLE)
            value = child.data(_VALUE_NODE_ROLE)
            kind = child.data(_TYPE_NODE_ROLE)
            raw_data = None
            if kind is GFFFieldType.Struct:
                value = GFFStruct(value)
                self._build_struct(child, value)
            elif kind is GFFFieldType.List:
                value = GFFList()
                self._build_list(child, value)
            elif kind is GFFFieldType.String and value == child.data(_ORIGINAL_NODE_ROLE):
                raw_data = child.data(_RAW_NODE_ROLE)
            gffStruct.add_field(label, kind, value, raw_data=raw_data)
            gffStruct._field_order[-1][1]._binary = child.data(_FIELD_BINARY_ROLE)

    def _build_list(self, item: QStandardItem, gffList: GFFList):
        """Builds a list of GFF structures from a QStandardItem tree.

        Args:
        ----
            item: QStandardItem: The root item of the tree
            gffList: GFFList: The list to populate.

        Processing Logic:
        ----------------
            - Loops through each child row of the item
            - Gets the struct ID from the child data
            - Adds a new GFF structure to the list with that ID
            - Recursively builds the child structure.
        """
        for i in range(item.rowCount()):
            child = item.child(i, 0)
            assert child is not None, f"child cannot be None in {self!r}_build_list({item!r}, {gffList!r})"
            struct_id = child.data(_VALUE_NODE_ROLE)
            gffStruct: GFFStruct = gffList.add(struct_id)
            self._build_struct(child, gffStruct)

    def new(self):
        super().new()
        self._content: GFFContent | None = None
        self._binary_data = b""
        self.model.clear()
        self.model.setColumnCount(1)

        rootNode = QStandardItem("[ROOT]")
        rootNode.setForeground(QBrush(QColor(0x660000)))
        self.model.appendRow(rootNode)

    def selectionChanged(self, selected: QItemSelectionRange):
        """Updates UI when selection changes in view.

        Args:
        ----
            selected: QItemSelectionRange: The currently selected item range

        Processes selection change:
            - Gets the proxy index of the selected item
            - Maps the proxy index to the source index
            - Gets the item from the source model using the source index
            - Loads the selected item into the UI.
        """
        proxyIndex = selected.indexes()[0]
        sourceIndex = self.proxyModel.mapToSource(proxyIndex)
        item = self.model.itemFromIndex(sourceIndex)
        assert item is not None
        self.loadItem(item)

    def loadItem(self, item: QListWidgetItem):
        """Load item into UI widgets.

        Args:
        ----
            item: QListWidgetItem: Item to load.

        Load item data into UI widgets:
            - Set current widget based on field type
            - Populate spinboxes, line edits, text edits with value
            - Populate dropdowns and lists
            - Handle specialized types like vectors and localized strings.
        """
        # Loading a value is not an edit (notably for undecodable text bytes).
        blockers = [QtCore.QSignalBlocker(widget) for widget in (
            self.ui.intSpin, self.ui.floatSpin, self.ui.lineEdit, self.ui.textEdit,
            self.ui.labelEdit, self.ui.typeCombo, self.ui.stringrefSpin,
            self.ui.substringList, self.ui.substringEdit,
            self.ui.xVec3Spin, self.ui.yVec3Spin, self.ui.zVec3Spin,
            self.ui.xVec4Spin, self.ui.yVec4Spin, self.ui.zVec4Spin, self.ui.wVec4Spin,
        )]
        def set_widget(arg0, arg1, item):
            self.ui.pages.setCurrentWidget(self.ui.intPage)
            self.ui.intSpin.setRange(arg0, arg1)
            self.ui.intSpin.setValue(item.data(_VALUE_NODE_ROLE))
        if item.data(_TYPE_NODE_ROLE) is None:  # Field-less struct (root or in list)
            self.ui.fieldBox.setEnabled(False)

            set_widget(-1, 0xFFFFFFFF, item)
        else:
            self.ui.fieldBox.setEnabled(True)
            self.ui.typeCombo.setCurrentText(item.data(_TYPE_NODE_ROLE).name)
            self.ui.labelEdit.setText(item.data(_LABEL_NODE_ROLE))

            if item.data(_TYPE_NODE_ROLE) in _INTEGER_RANGES:
                set_widget(*_INTEGER_RANGES[item.data(_TYPE_NODE_ROLE)], item)
            elif item.data(_TYPE_NODE_ROLE) in {GFFFieldType.Double, GFFFieldType.Single}:
                self.ui.pages.setCurrentWidget(self.ui.floatPage)
                self.ui.floatSpin.setValue(item.data(_VALUE_NODE_ROLE))
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.ResRef:
                self.ui.pages.setCurrentWidget(self.ui.linePage)
                self.ui.lineEdit.setText(str(item.data(_VALUE_NODE_ROLE)))
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.String:
                self.ui.pages.setCurrentWidget(self.ui.textPage)
                self.ui.textEdit.setPlainText(item.data(_VALUE_NODE_ROLE))
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Struct:
                set_widget(-1, 0xFFFFFFFF, item)
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.List:
                self.ui.pages.setCurrentWidget(self.ui.blankPage)
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Vector3:
                self.ui.pages.setCurrentWidget(self.ui.vector3Page)
                vec3 = item.data(_VALUE_NODE_ROLE)
                self.ui.xVec3Spin.setValue(vec3.x)
                self.ui.yVec3Spin.setValue(vec3.y)
                self.ui.zVec3Spin.setValue(vec3.z)
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Vector4:
                self.ui.pages.setCurrentWidget(self.ui.vector4Page)
                vec4 = item.data(_VALUE_NODE_ROLE)
                self.ui.xVec4Spin.setValue(vec4.x)
                self.ui.yVec4Spin.setValue(vec4.y)
                self.ui.zVec4Spin.setValue(vec4.z)
                self.ui.wVec4Spin.setValue(vec4.w)
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Binary:
                self.ui.pages.setCurrentWidget(self.ui.blankPage)
                ...  # TODO: Why is this here?
            elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.LocalizedString:
                locstring: LocalizedString = item.data(_VALUE_NODE_ROLE)
                self.ui.pages.setCurrentWidget(self.ui.substringPage)
                self.ui.substringEdit.setEnabled(False)
                self.ui.stringrefSpin.setValue(locstring.stringref)
                self.ui.substringList.clear()
                for language, gender, text in locstring:
                    if self.ui.substringLangCombo.findData(int(language)) == -1:
                        self.ui.substringLangCombo.addItem(f"Language {language}", int(language))
                    item = QListWidgetItem(f"{language.name.title() if isinstance(language, Language) else f'Language {language}'}, {gender.name.title()}")
                    item.setData(text, _TEXT_SUBSTRING_ROLE)
                    item.setData(_ID_SUBSTRING_ROLE, LocalizedString.substring_id(language, gender))
                    self.ui.substringList.addItem(item)

        del blockers

    def updateData(self):
        """Updates data in the GFF tree model.

        Args:
        ----
            self: The class instance

        Updates the selected item's:
        - Label
        - Value based on type:
            - Integer, float, string, vector, etc
        - Refreshes the item text
        """
        if not self.ui.treeView.selectedIndexes():
            return

        proxyIndex = self.ui.treeView.selectedIndexes()[0]
        sourceIndex = self.proxyModel.mapToSource(proxyIndex)
        item = self.model.itemFromIndex(sourceIndex)

        item.setData(self.ui.labelEdit.text(), _LABEL_NODE_ROLE)

        if item.data(_TYPE_NODE_ROLE) in {
            GFFFieldType.UInt8,
            GFFFieldType.Int8,
            GFFFieldType.UInt16,
            GFFFieldType.Int16,
            GFFFieldType.UInt32,
            GFFFieldType.Int32,
            GFFFieldType.UInt64,
            GFFFieldType.Int64,
        }:
            item.setData(self.ui.intSpin.value(), _VALUE_NODE_ROLE)
        elif item.data(_TYPE_NODE_ROLE) in {GFFFieldType.Single, GFFFieldType.Double}:
            item.setData(self.ui.floatSpin.value(), _VALUE_NODE_ROLE)
        elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.ResRef:
            if str(item.data(_VALUE_NODE_ROLE)) != self.ui.lineEdit.text():
                item.setData(ResRef(self.ui.lineEdit.text()), _VALUE_NODE_ROLE)
        elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.String:
            item.setData(self.ui.textEdit.toPlainText(), _VALUE_NODE_ROLE)
        elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Vector3:
            vec3 = Vector3(self.ui.xVec3Spin.value(), self.ui.yVec3Spin.value(), self.ui.zVec3Spin.value())
            item.setData(vec3, _VALUE_NODE_ROLE)
        elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Vector4:
            vec4 = Vector4(self.ui.xVec4Spin.value(), self.ui.yVec4Spin.value(), self.ui.zVec4Spin.value(), self.ui.wVec4Spin.value())
            item.setData(vec4, _VALUE_NODE_ROLE)
        elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.LocalizedString:
            item.data(_VALUE_NODE_ROLE).stringref = self.ui.stringrefSpin.value()
        elif item.data(_TYPE_NODE_ROLE) == GFFFieldType.Struct or item.data(_TYPE_NODE_ROLE) is None:
            item.setData(self.ui.intSpin.value(), _VALUE_NODE_ROLE)

        assert item is not None
        self.refreshItemText(item)

    def substringSelected(self):
        if len(self.ui.substringList.selectedItems()) > 0:
            item: QListWidgetItem = self.ui.substringList.selectedItems()[0]
            self.ui.substringEdit.setEnabled(True)
            self.ui.substringEdit.setPlainText(item.data(_TEXT_SUBSTRING_ROLE))
        else:
            self.ui.substringEdit.setEnabled(False)

    def substringEdited(self):
        """Edits the selected substring in the list.

        Args:
        ----
            self: The class instance.

        Processing Logic:
        ----------------
            - Get the selected substring item from the list
            - Get the edited text from the text edit field
            - Set the text data on the selected item
            - Get the selected substring item and language/gender pair
            - Get the selected localization string item
            - Set the edited text on the localization string
        """
        item: QListWidgetItem = self.ui.substringList.selectedItems()[0]
        text: str = self.ui.substringEdit.toPlainText()
        item.setData(text, _TEXT_SUBSTRING_ROLE)

        substringItem: QListWidgetItem = self.ui.substringList.selectedItems()[0]
        language, gender = LocalizedString.substring_pair(substringItem.data(_ID_SUBSTRING_ROLE))
        locstringItem: QModelIndex = self.ui.treeView.selectedIndexes()[0]
        locstring: LocalizedString = locstringItem.data(_VALUE_NODE_ROLE)
        locstring.set_data(language, gender, text)

    def addSubstring(self):
        """Adds a substring to the selected localized string.

        Processing Logic:
        ----------------
            - Gets the selected language and gender from combo boxes
            - Generates a unique substring ID based on language and gender
            - Loops through existing substring list to check for duplicate ID
            - If no duplicate, adds a new item to the substring list with the ID and empty text
            - Gets the selected localized string node from the tree view
            - Sets the empty substring data on the localized string based on the selected language and gender.
        """
        language = self.ui.substringLangCombo.currentData()
        gender = Gender(self.ui.substringGenderCombo.currentIndex())
        substringId = LocalizedString.substring_id(language, gender)

        for i in range(self.ui.substringList.count()):
            item = self.ui.substringList.item(i)
            if item.data(_ID_SUBSTRING_ROLE) == substringId:
                return

        item = QListWidgetItem(f"{language.name.title() if isinstance(language, Language) else f'Language {language}'}, {gender.name.title()}")
        item.setData(_ID_SUBSTRING_ROLE, substringId)
        item.setData(_TEXT_SUBSTRING_ROLE, "")
        self.ui.substringList.addItem(item)

        locstringItem = self.ui.treeView.selectedIndexes()[0]
        locstring: LocalizedString = locstringItem.data(_VALUE_NODE_ROLE)
        locstring.set_data(language, gender, "")

    def removeSubstring(self):
        """Removes a substring from a localized string.

        Args:
        ----
            language: The language of the substring to remove.
            gender: The gender of the substring to remove.

        Processing Logic:
        ----------------
            - Gets the substring ID from the language and gender indexes
            - Loops through the substring list backwards and removes any items with a matching ID
            - Gets the selected localized string from the tree view
            - Removes the substring from the localized string using the language and gender
        """
        locstringItem = self.ui.treeView.selectedIndexes()[0]
        locstring = locstringItem.data(_VALUE_NODE_ROLE)
        for item in self.ui.substringList.selectedItems():
            language, gender = LocalizedString.substring_pair(item.data(_ID_SUBSTRING_ROLE))
            locstring.remove(language, gender)
            self.ui.substringList.takeItem(self.ui.substringList.row(item))

    def refreshItemText(self, item: QStandardItem):
        """Refreshes the text of an item in the tree view.

        Args:
        ----
            item: QStandardItem: The item whose text needs to be refreshed.

        Refreshes the text of the item based on its data:
        - Sets the text based on the item's label, type and value
        - Sets the foreground color based on the item's type
        - Updates the item with the refreshed text.
        """
        label, ftype, value = item.data(_LABEL_NODE_ROLE), item.data(_TYPE_NODE_ROLE), item.data(_VALUE_NODE_ROLE)

        if ftype is None and item.parent() is None:
            text = "[ROOT]"
        elif ftype is None:
            text = f'{str(item.row()).ljust(16)} {"[Struct]".ljust(17)} = {value}'
        elif ftype is GFFFieldType.Struct:
            text = f'{label.ljust(16)} {"[Struct]".ljust(17)} = {value}'
        elif ftype is GFFFieldType.List:
            text = f'{label.ljust(16)} {"[List]".ljust(17)} = {item.rowCount()}'
        else:
            text = f'{label.ljust(16)} {f"[{ftype.name}]".ljust(17)} = {value}'

        if ftype == GFFFieldType.Struct or ftype is None:
            item.setForeground(QBrush(QColor(0x660000)))
        elif ftype == GFFFieldType.List:
            item.setForeground(QBrush(QColor(0x000088)))
        else:
            item.setForeground(QBrush(QColor(0x000000)))

        item.setText(text)

    def typeChanged(self, ftypeId: int):
        """Commit a type and a representable value together, preserving rejected edits."""
        indexes = self.ui.treeView.selectedIndexes()
        if not indexes:
            return
        item = self.model.itemFromIndex(self.proxyModel.mapToSource(indexes[0]))
        ftype = GFFFieldType(ftypeId)
        if ftype == item.data(_TYPE_NODE_ROLE):
            return
        try:
            value = self._convertedValue(ftype, item.data(_VALUE_NODE_ROLE))
        except (ValueError, OverflowError) as error:
            QMessageBox.warning(self, "Cannot change field type", str(error))
            self.loadItem(item)
            return
        if item.rowCount() and QMessageBox.question(
            self, "Remove child fields?", "Changing this field's type will remove its child fields. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            self.loadItem(item)
            return
        item.removeRows(0, item.rowCount())
        item.setData(ftype, _TYPE_NODE_ROLE)
        item.setData(value, _VALUE_NODE_ROLE)
        item.setData(None, _RAW_NODE_ROLE)
        item.setData(None, _ORIGINAL_NODE_ROLE)
        self.loadItem(item)
        self.refreshItemText(item)

    def _convertedValue(self, ftype, value):
        """Return a value matching the tree model's representation of the new type."""
        numeric = isinstance(value, (int, float))
        if ftype in _INTEGER_RANGES:
            if not numeric:
                return 0
            if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
                raise ValueError(f"{value!r} is not an integer.")
            converted = int(value)
            minimum, maximum = _INTEGER_RANGES[ftype]
            if not minimum <= converted <= maximum:
                raise ValueError(f"{value!r} is outside the {ftype.name} range ({minimum} to {maximum}).")
            return converted
        if ftype in (GFFFieldType.Single, GFFFieldType.Double):
            converted = float(value) if numeric else 0.0
            if not math.isfinite(converted):
                raise ValueError("Choose a finite numeric value before changing its type.")
            if ftype == GFFFieldType.Single:
                try:
                    converted = struct.unpack("<f", struct.pack("<f", converted))[0]
                except (OverflowError, struct.error) as error:
                    raise ValueError("The value is outside the Single precision range.") from error
            return converted
        if ftype == GFFFieldType.Struct:
            return 0  # The value role holds the struct ID; fields are child nodes.
        if ftype == GFFFieldType.List:
            return None  # List elements are child nodes, not a value-role object.
        if ftype == GFFFieldType.String:
            return ""
        if ftype == GFFFieldType.LocalizedString:
            return LocalizedString.from_invalid()
        if ftype == GFFFieldType.ResRef:
            return ResRef.from_blank()
        if ftype == GFFFieldType.Vector3:
            return Vector3.from_null()
        if ftype == GFFFieldType.Vector4:
            return Vector4.from_null()
        if ftype == GFFFieldType.Binary:
            return b""
        raise ValueError(f"Unsupported field type: {ftype!r}.")

    def insertNode(self, parent: QStandardItem, label: str, ftype: GFFFieldType | None, value: Any):
        """Inserts a new child node under the given parent node.

        Args:
        ----
            parent: The parent QStandardItem node to insert under
            label: The label of the new node
            ftype: The field type of the new node
            value: The value of the new node

        Processing Logic:
        ----------------
            1. Creates a new empty QStandardItem
            2. Sets the label, type and value data roles on the new item
            3. Appends the new item as a child to the parent node
            4. Refreshes the text of the new item.
        """
        item = QStandardItem("")
        item.setData(label, _LABEL_NODE_ROLE)
        item.setData(ftype, _TYPE_NODE_ROLE)
        item.setData(value, _VALUE_NODE_ROLE)
        parent.appendRow(item)
        self.refreshItemText(item)

    def removeNode(self, item: QStandardItem):
        """Remove a node from the tree model.

        Args:
        ----
            item: The item to remove

        Processing Logic:
        ----------------
            - Get the parent item of the item to remove
            - Remove the item's row from the parent
            - This removes the item from the model.
        """
        item.parent().removeRow(item.row())

    def removeSelectedNodes(self):
        """Removes selected nodes from the tree.

        Args:
        ----
            self: The class instance.

        Processing Logic:
        ----------------
            - Loops through the selected indexes in the tree view.
            - Maps each proxy index to its corresponding source index.
            - Gets the item from the source model using the source index.
            - Calls removeNode() to remove the item from the model.
        """
        for proxyIndex in self.ui.treeView.selectedIndexes():
            sourceIndex = self.proxyModel.mapToSource(proxyIndex)
            item = self.model.itemFromIndex(sourceIndex)
            assert item is not None
            self.removeNode(item)

    def requestContextMenu(self, point):
        """Generates context menu for tree view item at given point.

        Args:
        ----
            point: Point in global coordinates

        Processing Logic:
        ----------------
            - Gets proxy and source index for item at point
            - Gets item from model using source index
            - Builds context menu with actions depending on item type
            - Connects actions to methods
            - Pops up menu at point.
        """
        proxyIndex = self.ui.treeView.indexAt(point)
        sourceIndex = self.proxyModel.mapToSource(proxyIndex)
        item = self.model.itemFromIndex(sourceIndex)

        if item is not None:
            menu = QMenu(self)

            if item.data(_TYPE_NODE_ROLE) == GFFFieldType.List:
                menu.addAction("Add Struct").triggered.connect(lambda: self.insertNode(item, "", None, 0))
            elif item.data(_TYPE_NODE_ROLE) in [GFFFieldType.Struct, None]:
                self._build_context_menu_gff_struct(menu, item)

            if item.data(_TYPE_NODE_ROLE) is not None:
                menu.addAction("Remove").triggered.connect(lambda: self.removeNode(item))

            menu.popup(self.ui.treeView.viewport().mapToGlobal(point))

    def _build_context_menu_gff_struct(self, menu: QMenu, item: QStandardItem):
        """Create fields with the same model representation used by type conversion."""
        for ftype in (
            GFFFieldType.UInt8, GFFFieldType.UInt16, GFFFieldType.UInt32, GFFFieldType.UInt64,
            GFFFieldType.Int8, GFFFieldType.Int16, GFFFieldType.Int32, GFFFieldType.Int64,
            GFFFieldType.Single, GFFFieldType.Double, GFFFieldType.ResRef, GFFFieldType.String,
            GFFFieldType.LocalizedString, GFFFieldType.Binary, GFFFieldType.Vector3, GFFFieldType.Vector4,
            GFFFieldType.Struct, GFFFieldType.List,
        ):
            if ftype == GFFFieldType.Struct:
                menu.addSeparator()
            menu.addAction(f"Add {ftype.name}").triggered.connect(
                lambda checked=False, ftype=ftype: self.insertNode(
                    item, f"New {ftype.name}"[:16], ftype, self._convertedValue(ftype, None),
                ),
            )
        menu.addSeparator()

    def selectTalkTable(self):
        """Select a TLK file using a file dialog.

        Args:
        ----
            self: The class instance

        Processing Logic:
        ----------------
            - Open a file dialog to select a TLK file
            - Get the selected file path and filter from the dialog
            - If a file is selected, load it as a TalkTable and assign to self._talktable.
        """
        filepath, filter = QFileDialog.getOpenFileName(self, "Select a TLK file", "", "TalkTable (*.tlk)")
        if filepath:
            self._talktable = TalkTable(filepath)

    def changeLocstringText(self):
        """Changes the text displayed based on the selected string reference.

        Args:
        ----
            self: The class instance

        Processing Logic:
        ----------------
            - Checks if talktable is not None
            - Gets the string from talktable based on the selected string reference value
            - Sets the text edit plain text to the retrieved string
            - If talktable is None, sets the text edit plain text to empty string.
        """
        if self._talktable is not None:
            text = self._talktable.string(self.ui.stringrefSpin.value())
            self.ui.tlkTextEdit.setPlainText(text)
        else:
            self.ui.tlkTextEdit.setPlainText("")


class GFFSortFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent: QWidget):
        super().__init__(parent)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Compares two model indexes and returns whether the left is less than the right.

        Args:
        ----
            left: {QModelIndex}: The left model index to compare
            right: {QModelIndex}: The right model index to compare

        Processing Logic:
        ----------------
            - Extract text from each index using _LABEL_NODE_ROLE and _VALUE_NODE_ROLE roles
            - If both texts are digits, convert to ints and compare numerically
            - Otherwise compare strings alphabetically.
        """
        leftText: str = self.sourceModel().itemFromIndex(left).data(_LABEL_NODE_ROLE)
        rightText: str = self.sourceModel().itemFromIndex(right).data(_LABEL_NODE_ROLE)

        leftText = leftText or str(self.sourceModel().itemFromIndex(left).data(_VALUE_NODE_ROLE))
        rightText = rightText or str(self.sourceModel().itemFromIndex(right).data(_VALUE_NODE_ROLE))

        if leftText.isdigit() and rightText.isdigit():
            leftInt = int(leftText)
            rightInt = int(rightText)
            return leftInt < rightInt
        return leftText < rightText
