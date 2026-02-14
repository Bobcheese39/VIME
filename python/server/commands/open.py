"""Open command handler."""

import logging
import os

logger = logging.getLogger("vime")


def _normalize_path(path):
    """Normalize a path for same-file comparisons."""
    return os.path.normcase(os.path.abspath(path or ""))


def handle(state, payload):
    """Open an HDF5 file and return the list of tables."""
    filepath = payload.get("file", "")
    logger.info("Opening file: %s", filepath)
    if not filepath or not os.path.isfile(filepath):
        logger.warning("File not found: %s", filepath)
        return {"ok": False, "error": f"File not found: {filepath}"}

    if state.loader.is_open and _normalize_path(state.loader.filepath) == _normalize_path(filepath):
        logger.info("Reusing already open file: %s", filepath)
        return {"ok": True, "tables": state.get_table_list()}

    state.close_handles()
    state.current_df = None
    state.current_table = None

    try:
        state.loader.open(filepath)
    except Exception as exc:
        logger.exception("Failed to open file: %s", filepath)
        return {"ok": False, "error": str(exc)}
    logger.info("File opened: %s", filepath)
    return {"ok": True, "tables": state.get_table_list()}
