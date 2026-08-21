"""Checks for persistent column order and hidden columns."""

import json
import os
import tempfile
import unittest

from config import Config


class ConfigHiddenTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "config.json")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_legacy_list_hide_and_merge_skip_hidden(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"/t": ["a", "b", "c"]}, handle)
        cfg = Config(self.path)
        self.assertEqual(cfg.get_columns("/t"), ["a", "b", "c"])
        self.assertEqual(cfg.get_hidden("/t"), [])
        self.assertTrue(cfg.toggle_hidden("/t", "b"))
        self.assertEqual(cfg.get_columns("/t"), ["a", "c"])
        self.assertEqual(cfg.get_hidden("/t"), ["b"])
        self.assertEqual(
            cfg.merge_table_columns("/t", ["a", "b", "c", "d"]),
            ["a", "c", "d"],
        )
        self.assertEqual(cfg.get_hidden("/t"), ["b"])

        with open(self.path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(stored["/t"]["hidden"], ["b"])

        reloaded = Config(self.path)
        self.assertEqual(reloaded.get_columns("/t"), ["a", "c", "d"])
        self.assertEqual(reloaded.get_hidden("/t"), ["b"])
        self.assertFalse(reloaded.toggle_hidden("/t", "b"))
        self.assertEqual(reloaded.get_hidden("/t"), [])
        self.assertEqual(reloaded.get_columns("/t"), ["a", "c", "d", "b"])


if __name__ == "__main__":
    unittest.main()
