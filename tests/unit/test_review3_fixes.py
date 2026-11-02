"""Inventory repair, GIT template lookup, and lazy editor dispatch regressions."""
import builtins
from pathlib import Path
from types import MethodType, ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication
from pykotor.common.misc import EquipmentSlot, InventoryItem, ResRef
from pykotor.extract.file import ResourceIdentifier
from pykotor.resource.type import ResourceType
from toolset.data.installation import HTInstallation
from toolset.gui.dialogs import inventory
from toolset.gui.editor import Editor
from toolset.gui.editors import git
from toolset.utils import window


class InventoryLookupTests(TestCase):
    def setUp(self):
        self.installation = SimpleNamespace(resource=Mock(return_value=None), string=lambda value, fallback: value)
        self.editor = SimpleNamespace(_installation=self.installation)

    def test_missing_templates_retain_their_reference(self):
        for path in ("", "/game/data/items.bif", "/game/modules/demo.mod"):
            with self.subTest(path=path), patch.object(inventory, "Capsule") as capsule, \
                 patch.object(inventory, "read_uti") as read:
                capsule.return_value.resource.return_value = None
                filepath, name, uti = inventory.InventoryEditor.getItem(self.editor, "missing_item", path)
                self.assertEqual(filepath, path)
                self.assertIn("missing_item", name)
                self.assertIsNone(uti)
                read.assert_not_called()

    def test_missing_loose_file_uses_a_placeholder(self):
        with patch.object(inventory.BinaryReader, "load_file", side_effect=FileNotFoundError):
            path, name, uti = inventory.InventoryEditor.getItem(self.editor, "missing_item", "/tmp/missing_item.uti")
        self.assertEqual(path, "/tmp/missing_item.uti")
        self.assertIn("missing_item", name)
        self.assertIsNone(uti)

    def test_found_templates_use_their_display_name(self):
        self.installation.resource.return_value = SimpleNamespace(filepath=Path("/game/Override/item.uti"), data=b"template")
        template = SimpleNamespace(name="Item name")
        with patch.object(inventory, "read_uti", return_value=template):
            self.assertEqual(inventory.InventoryEditor.getItem(self.editor, "item", ""),
                             ("/game/Override/item.uti", "Item name", template))

    def test_blank_reference_does_not_query_installation(self):
        self.assertEqual(inventory.InventoryEditor.getItem(self.editor, "", ""), ("", "", None))
        self.installation.resource.assert_not_called()

    def test_placeholder_icon_does_not_read_baseitems(self):
        installation = SimpleNamespace(htGetCache2DA=Mock(side_effect=AssertionError("unnecessary lookup")))
        with patch("toolset.data.installation.QPixmap", return_value="placeholder"):
            self.assertEqual(HTInstallation.getItemIconFromUTI(installation, None), "placeholder")


class InventoryEditingTests(TestCase):
    def setUp(self):
        self.frame = inventory.ItemContainer(True, True)
        self.frame.setItem("old_item", "", "Old item", True, True)
        self.frame.slot = EquipmentSlot.HEAD
        self.label = Mock()
        self.editor = SimpleNamespace(
            _slotMap={self.frame.slot: SimpleNamespace(label=self.label, frame=self.frame, emptyImage="{}_head")},
            _droid=False, getItem=Mock(return_value=("", "[Missing item: custom_item]", None)),
            getItemImage=Mock(return_value="placeholder"),
        )
        self.editor.setEquipment = MethodType(inventory.InventoryEditor.setEquipment, self.editor)

    def test_custom_missing_reference_remains_equipped_with_flags(self):
        with patch.object(inventory.QInputDialog, "getText", return_value=(" custom_item ", True)):
            inventory.InventoryEditor.promptSetItemResRefDialog(self.editor, self.frame)
        self.assertEqual(self.frame.resname, "custom_item")
        self.assertTrue(self.frame.hasItem)
        self.assertTrue(self.frame.droppable)
        self.assertTrue(self.frame.infinite)
        self.label.setPixmap.assert_called_with("placeholder")

    def test_cancelled_prompt_keeps_the_original_slot(self):
        with patch.object(inventory.QInputDialog, "getText", return_value=("replacement", False)):
            inventory.InventoryEditor.promptSetItemResRefDialog(self.editor, self.frame)
        self.assertEqual(self.frame.resname, "old_item")
        self.editor.getItem.assert_not_called()

    def test_empty_reference_clears_the_slot(self):
        with patch.object(inventory.QInputDialog, "getText", return_value=("", True)), \
             patch.object(inventory, "QPixmap", return_value="empty slot"):
            inventory.InventoryEditor.promptSetItemResRefDialog(self.editor, self.frame)
        self.assertFalse(self.frame.hasItem)
        self.assertEqual(self.frame.resname, "")
        self.assertFalse(self.frame.droppable)
        self.assertFalse(self.frame.infinite)
        self.editor.getItem.assert_not_called()


class GITTemplateTests(TestCase):
    def setUp(self):
        self.identifier = ResourceIdentifier("template", ResourceType.UTC)
        self.instance = SimpleNamespace(identifier=lambda: self.identifier)
        self.parent = SimpleNamespace(filepath=lambda: "/game/modules/demo.rim", resourceSaved=Mock())
        self.installation = SimpleNamespace(location=Mock(return_value=[]), resource=Mock(return_value=None),
                                            override_path=lambda: Path("/game/Override"))
        self.mode = SimpleNamespace(_ui=SimpleNamespace(renderArea=SimpleNamespace(
            instanceSelection=SimpleNamespace(all=lambda: [self.instance]))), _editor=self.parent, _installation=self.installation)
        self.child = Mock(spec=Editor)
        self.opened = patch.object(git, "openResourceEditor", return_value=(None, self.child)).start()
        self.addCleanup(patch.stopall)
        self.read = patch.object(git, "getResourceFromFile", return_value=b"template bytes").start()

    def locations(self, *paths):
        self.installation.location.return_value = [SimpleNamespace(filepath=Path(path)) for path in paths]

    def test_current_module_mod_is_preferred_to_rim_and_other_modules(self):
        self.locations("/game/modules/other.mod", "/game/modules/demo.rim", "/game/modules/demo.mod")
        git._InstanceMode.editSelectedInstanceResource(self.mode)
        self.assertEqual(self.opened.call_args.args[0], Path("/game/modules/demo.mod"))
        self.child.savedFile.connect.assert_called_once_with(self.parent.resourceSaved)
        self.child.childSavedFile.connect.assert_called_once_with(self.parent.resourceSaved)

    def test_override_is_preferred_to_the_current_module(self):
        self.locations("/game/modules/demo.mod", "/game/Override/template.utc")
        git._InstanceMode.editSelectedInstanceResource(self.mode)
        self.assertEqual(self.opened.call_args.args[0], Path("/game/Override/template.utc"))

    def test_rim_template_can_be_opened(self):
        self.locations("/game/modules/demo.rim")
        git._InstanceMode.editSelectedInstanceResource(self.mode)
        self.assertEqual(self.opened.call_args.args[0], Path("/game/modules/demo.rim"))

    def test_core_template_reads_the_resource_payload(self):
        self.locations("/game/data/templates.bif")
        self.installation.resource.return_value = SimpleNamespace(filepath=Path("/game/data/templates.bif"), data=b"payload")
        git._InstanceMode.editSelectedInstanceResource(self.mode)
        self.assertEqual(self.opened.call_args.args[3], b"payload")
        self.read.assert_not_called()

    def test_unrelated_module_is_not_used_as_a_template(self):
        self.locations("/game/modules/other.mod")
        with patch.object(git.QMessageBox, "warning") as warning:
            git._InstanceMode.editSelectedInstanceResource(self.mode)
        self.opened.assert_not_called()
        warning.assert_called_once()

    def test_untitled_document_can_open_core_templates(self):
        self.parent.filepath = lambda: None
        self.test_core_template_reads_the_resource_payload()

    def test_saved_template_bytes_refresh_names_tags_and_visible_labels(self):
        identifier = self.identifier
        item = SimpleNamespace(data=lambda role: self.instance)
        mode = Mock(spec=git._InstanceMode)
        editor = SimpleNamespace(nameBuffer={identifier: "old name"}, tagBuffer={identifier: "old tag"},
            _git=SimpleNamespace(instances=lambda: [self.instance]), _mode=mode,
            _installation=SimpleNamespace(string=lambda value: value, override_path=lambda: Path("/game/Override"),
                                          reload_override_file=Mock()),
            ui=SimpleNamespace(listWidget=SimpleNamespace(count=lambda: 1, item=lambda row: item)))
        with patch.object(git, "extract_name", return_value="new name"), \
             patch.object(git, "extract_tag_from_gff", return_value="new tag"):
            git.GITEditor._onTemplateSaved(editor, "/game/Override/template.utc", "TEMPLATE", ResourceType.UTC, b"updated")
        self.assertEqual(editor.nameBuffer[identifier], "new name")
        self.assertEqual(editor.tagBuffer[identifier], "new tag")
        editor._installation.reload_override_file.assert_called_once_with(Path("/game/Override/template.utc"))
        mode.setListItemLabel.assert_called_once_with(item, self.instance)

    def test_loading_another_git_clears_old_template_caches(self):
        renderer = Mock()
        editor = SimpleNamespace(nameBuffer={"old": "name"}, tagBuffer={"old": "tag"}, history=Mock(),
                                 ui=SimpleNamespace(renderArea=renderer), _installation=self.installation, updateVisibility=Mock())
        document = object()
        with patch.object(git, "_InstanceMode"):
            git.GITEditor._loadGIT(editor, document)
        self.assertEqual(editor.nameBuffer, {})
        self.assertEqual(editor.tagBuffer, {})
        renderer.setGit.assert_called_once_with(document)

    def test_untitled_editor_filepath_is_none(self):
        self.assertIsNone(Editor.filepath(SimpleNamespace(_filepath=None)))


class EditorDispatchTests(TestCase):
    def check_route(self, restype, module, class_name, installation=None, specialized=True):
        created = SimpleNamespace(load=Mock())
        factory = Mock(return_value=created)
        selected = ModuleType(module)
        setattr(selected, class_name, factory)
        imports = []
        original_import = builtins.__import__
        def import_selected(name, globals=None, locals=None, fromlist=(), level=0):
            if name.startswith("toolset.gui.editors.") or name == "toolset.gui.windows.audio_player":
                imports.append(name)
                self.assertEqual(name, module)
                return selected
            return original_import(name, globals, locals, fromlist, level)
        with patch("builtins.__import__", side_effect=import_selected), patch.object(window, "addWindow") as add:
            result = window.openResourceEditor("/tmp/example." + restype.extension, "example", restype, b"data",
                                               installation, object(), specialized)
        self.assertEqual(imports, [module])
        self.assertIs(result[1], created)
        created.load.assert_called_once()
        add.assert_called_once_with(created)

    def test_text_and_standalone_script_only_import_the_text_editor(self):
        for restype in (ResourceType.TXT, ResourceType.NSS):
            self.check_route(restype, "toolset.gui.editors.txt", "TXTEditor")

    def test_specialized_and_generic_gff_routing(self):
        self.check_route(ResourceType.UTC, "toolset.gui.editors.gff", "GFFEditor")
        self.check_route(ResourceType.UTC, "toolset.gui.editors.gff", "GFFEditor", object(), False)
        self.check_route(ResourceType.UTC, "toolset.gui.editors.utc", "UTCEditor", object())

    def test_models_import_the_model_editor_when_selected(self):
        self.check_route(ResourceType.MDX, "toolset.gui.editors.mdl", "MDLEditor")


class QtInventoryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owns_app = QApplication.instance() is None
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        if cls.owns_app:
            cls.app.quit()
            cls.app.deleteLater()
        cls.app = None

    def test_missing_inventory_and_equipment_survive_accept(self):
        item = InventoryItem(ResRef("missing_item"))
        item.droppable, item.infinite = True, True
        installation = SimpleNamespace(resource=lambda *args: None, getItemIconFromUTI=lambda uti: inventory.QPixmap())
        with patch.object(inventory.InventoryEditor, "buildItems"):
            editor = inventory.InventoryEditor(None, installation, [], [], [item], {EquipmentSlot.HEAD: item})
        self.addCleanup(editor.close)
        editor.accept()
        for saved in (editor.inventory[0], editor.equipment[EquipmentSlot.HEAD]):
            self.assertEqual(str(saved.resref), "missing_item")
            self.assertTrue(saved.droppable)
            self.assertTrue(saved.infinite)

    def test_override_filter_is_connected(self):
        installation = SimpleNamespace(resource=lambda *args: None)
        with patch.object(inventory.InventoryEditor, "buildItems"), \
             patch.object(inventory.InventoryEditor, "doSearch") as search:
            editor = inventory.InventoryEditor(None, installation, [], [], [], {})
        self.addCleanup(editor.close)
        editor.ui.overrideSearchEdit.textEdited.emit("query")
        search.assert_called_once_with("query")
