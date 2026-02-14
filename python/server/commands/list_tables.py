"""List tables from the currently open datasource."""

import logging
import os

logger = logging.getLogger("vime")


def _normalize_path(path):
    """Normalize a path for same-file comparisons."""
    return os.path.normcase(os.path.abspath(path or ""))


def handle(state, payload):
    """Return table metadata from the already-open datasource."""
    requested = payload.get("file", "")
    current = state.loader.filepath

    if not state.loader.is_open:
        logger.warning("List tables requested without an open datasource")
        return {"ok": False, "code": "no_file_open", "error": "No file is open"}

    if requested and _normalize_path(requested) != _normalize_path(current):
        logger.warning("List tables file mismatch: requested=%s open=%s", requested, current)
        return {
            "ok": False,
            "code": "file_mismatch",
            "error": f"Open file does not match request: {requested}",
        }

    return {"ok": True, "tables": state.get_table_list()}
