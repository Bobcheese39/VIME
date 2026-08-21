#!/usr/bin/env python3
"""
JSON-backed table column configuration for VIME.

Schema (current):
{
  "<table_name>": {
    "columns": ["col_a", "col_b"],
    "hidden": ["col_c"]
  }
}

Legacy list form `{ "<table_name>": ["col_a", ...] }` is still accepted.
Tables with no hidden columns are written back as that list.
"""

import json
import logging
import os
from typing import Dict, List, Optional


logger = logging.getLogger("vime.config")


def _unique_strings(values) -> List[str]:
    if not isinstance(values, list):
        return []
    seen = set()
    ordered = []
    for col in values:
        if not isinstance(col, str) or col in seen:
            continue
        seen.add(col)
        ordered.append(col)
    return ordered


class Config:
    """Load and persist per-table column order and hidden columns."""

    def __init__(self, path: Optional[str] = None):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = path or os.path.join(root_dir, "config.json")
        self._tables: Dict[str, Dict[str, List[str]]] = {}
        self._load()

    def _load(self):
        """Load config from disk with safe fallback on errors."""
        if not os.path.isfile(self.path):
            self._tables = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._tables = self._sanitize(data)
            logger.debug("Loaded table config: %s (%d tables)", self.path, len(self._tables))
        except Exception as exc:
            logger.warning("Failed to load config %s: %s", self.path, exc)
            self._tables = {}

    @staticmethod
    def _sanitize(data) -> Dict[str, Dict[str, List[str]]]:
        """Accept list or {columns, hidden} entries; drop duplicates."""
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Dict[str, List[str]]] = {}
        for table_name, value in data.items():
            if not isinstance(table_name, str):
                continue
            if isinstance(value, list):
                columns, hidden = _unique_strings(value), []
            elif isinstance(value, dict):
                columns = _unique_strings(value.get("columns"))
                hidden = [col for col in _unique_strings(value.get("hidden")) if col not in columns]
            else:
                continue
            out[table_name] = {"columns": columns, "hidden": hidden}
        return out

    def save(self):
        """Persist config atomically. List form when a table has no hidden columns."""
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {}
        for table_name, entry in self._tables.items():
            if entry["hidden"]:
                payload[table_name] = {
                    "columns": entry["columns"],
                    "hidden": entry["hidden"],
                }
            else:
                payload[table_name] = entry["columns"]
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self.path)

    def get_columns(self, table_name: str) -> Optional[List[str]]:
        entry = self._tables.get(table_name)
        if entry is None:
            return None
        return list(entry["columns"])

    def get_hidden(self, table_name: str) -> List[str]:
        entry = self._tables.get(table_name)
        if entry is None:
            return []
        return list(entry["hidden"])

    def toggle_hidden(self, table_name: str, column: str) -> bool:
        """Move *column* between visible and hidden. Returns True if now hidden."""
        column = str(column)
        entry = self._tables.setdefault(table_name, {"columns": [], "hidden": []})
        if column in entry["hidden"]:
            entry["hidden"].remove(column)
            if column not in entry["columns"]:
                entry["columns"].append(column)
            self.save()
            return False
        if column in entry["columns"]:
            entry["columns"].remove(column)
        if column not in entry["hidden"]:
            entry["hidden"].append(column)
        self.save()
        return True

    def merge_table_columns(self, table_name: str, discovered_columns: List[str]) -> List[str]:
        """
        Merge discovered columns into stored visible order and persist when changed.

        Existing visible order is preserved. Newly discovered columns are appended
        unless they are hidden.
        """
        discovered = _unique_strings([str(col) for col in discovered_columns])
        entry = self._tables.get(table_name)
        if entry is None:
            self._tables[table_name] = {"columns": list(discovered), "hidden": []}
            self.save()
            return list(discovered)

        hidden = set(entry["hidden"])
        updated = list(entry["columns"])
        changed = False
        for col in discovered:
            if col not in updated and col not in hidden:
                updated.append(col)
                changed = True

        if changed:
            entry["columns"] = updated
            self.save()

        return list(entry["columns"])
