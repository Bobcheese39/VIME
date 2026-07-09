"""List tables from the currently open datasource."""

import logging

logger = logging.getLogger("vime")


def handle(state, payload):
    """Return table metadata from the already-open datasource."""
    session = state.session_for(payload)
    if session is None:
        logger.warning("List tables requested without an open datasource")
        return {"ok": False, "code": "no_file_open", "error": "No file is open"}

    try:
        session.ensure_open()
    except Exception as exc:
        logger.warning("Failed to reopen session %s: %s", session.filepath, exc)
        return {"ok": False, "code": "no_file_open", "error": str(exc)}

    return {"ok": True, "tables": session.get_table_list()}
