#!/usr/bin/env python3
"""
JSON-backed table column configuration for VIME.

Schema:
{
  "<table_name>": ["col_a", "col_b", ...]
}
"""

import json
import logging
import os
from typing import Dict, List, Optional


logger = logging.getLogger("vime.config")


class Config:
    """Load and persist per-table ordered column configuration."""

    def __init__(self, path: Optional[str] = None):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = path or os.path.join(root_dir, "config.json")
        self._tables: Dict[str, List[str]] = {}
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
            logger.info("Loaded table config: %s (%d tables)", self.path, len(self._tables))
        except Exception as exc:
            logger.warning("Failed to load config %s: %s", self.path, exc)
            self._tables = {}

    @staticmethod
    def _sanitize(data) -> Dict[str, List[str]]:
        """Keep only dict[str, list[str]] with duplicate columns removed."""
        if not isinstance(data, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for table_name, cols in data.items():
            if not isinstance(table_name, str) or not isinstance(cols, list):
                continue
            seen = set()
            ordered = []
            for col in cols:
                if not isinstance(col, str):
                    continue
                if col in seen:
                    continue
                seen.add(col)
                ordered.append(col)
            out[table_name] = ordered
        return out

    def save(self):
        """Persist config atomically."""
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(self._tables, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self.path)

    def get_columns(self, table_name: str) -> Optional[List[str]]:
        cols = self._tables.get(table_name)
        if cols is None:
            return None
        return list(cols)

    def merge_table_columns(self, table_name: str, discovered_columns: List[str]) -> List[str]:
        """
        Merge discovered columns into stored order and persist when changed.

        Existing order is preserved and newly discovered columns are appended.
        """
        discovered = []
        seen = set()
        for col in discovered_columns:
            col_name = str(col)
            if col_name in seen:
                continue
            seen.add(col_name)
            discovered.append(col_name)

        current = self._tables.get(table_name, [])
        updated = list(current)
        changed = False

        for col in discovered:
            if col not in current:
                updated.append(col)
                changed = True

        if table_name not in self._tables:
            updated = discovered
            changed = True

        if changed:
            self._tables[table_name] = updated
            self.save()

        return list(self._tables.get(table_name, discovered))