"""Undo/redo retains actual game-instance occurrences and edit boundaries."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from PyQt5.QtWidgets import QApplication
from pykotor.resource.generics.git import GIT, GITPlaceable
from toolset.data.instance_history import InstanceEditHistory, clone_instance


class InstanceHistoryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.git = GIT()
        self.record = GITPlaceable(1.0, 2.0, 3.0)
        self.git.placeables.append(self.record)
        self.history = InstanceEditHistory(None, Mock())
        self.history.reset(self.git)

    def test_move_undo_redo_keeps_same_record(self):
        with self.history.edit("Move", [self.record]):
            self.record.position.x = 10.0
        self.history.undo()
        self.assertIs(self.git.placeables[0], self.record)
        self.assertEqual(self.record.position.x, 1.0)
        self.history.redo()
        self.assertEqual(self.record.position.x, 10.0)

    def test_added_instance_membership_is_undoable(self):
        duplicate = clone_instance(self.record)
        with self.history.edit("Duplicate", membership=True):
            self.git.placeables.append(duplicate)
        self.history.undo()
        self.assertEqual(self.git.placeables, [self.record])
        self.history.redo()
        self.assertIs(self.git.placeables[1], duplicate)

    def test_failed_edit_restores_values_and_membership(self):
        with self.assertRaises(ValueError):
            with self.history.edit("Move", [self.record], membership=True):
                self.record.position.x = 99.0
                self.git.placeables.clear()
                raise ValueError("cancel")
        self.assertEqual(self.git.placeables, [self.record])
        self.assertEqual(self.record.position.x, 1.0)
        self.assertFalse(self.history.canUndo())

    def test_unchanged_gesture_adds_no_undo_command(self):
        with self.history.edit("Move", [self.record]):
            pass
        self.assertFalse(self.history.canUndo())

    def test_cloned_occurrence_gets_independent_source_identity(self):
        record = SimpleNamespace(_gff_key="original", _gff_record=SimpleNamespace(_source_key="original"))
        result = clone_instance(record)
        self.assertNotEqual(result._gff_key, record._gff_key)
        self.assertEqual(result._gff_record._source_key, result._gff_key)
        self.assertEqual(record._gff_record._source_key, "original")
