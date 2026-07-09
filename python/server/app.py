"""Command dispatch — routes incoming commands to their handlers."""

import logging

from server.commands import open as cmd_open
from server.commands import list_tables as cmd_list_tables
from server.commands import table as cmd_table
from server.commands import plot as cmd_plot
from server.commands import info as cmd_info
from server.commands import compute as cmd_compute

logger = logging.getLogger("vime")


def dispatch(state, payload):
    """Route a command dict to the appropriate handler."""
    cmd = payload.get("cmd", "")
    logger.debug("Dispatch command: %s", cmd)
    handlers = {
        "open": cmd_open.handle,
        "list_tables": cmd_list_tables.handle,
        "table": cmd_table.handle,
        "info": cmd_info.handle,
        "plot_start": cmd_plot.handle_start,
        "plot_status": cmd_plot.handle_status,
        "compute_start": cmd_compute.handle_start,
        "compute_status": cmd_compute.handle_status,
    }
    handler = handlers.get(cmd)
    if handler is None:
        logger.warning("Unknown command: %s", cmd)
        return {"ok": False, "error": f"Unknown command: {cmd}"}
    try:
        return handler(state, payload)
    except Exception as exc:
        logger.exception("Command failed: %s", cmd)
        return {"ok": False, "error": str(exc)}
