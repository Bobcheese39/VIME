"""Background compute command handlers."""

import logging
import time
import threading

from server.state import ComputeState, ComputeStatus
from test_compute import test_compute

logger = logging.getLogger("vime")


def handle_start(state, _payload):
    """Start a background compute job using test_compute()."""
    if state.compute_thread is not None and state.compute_thread.is_alive():
        logger.warning("Compute start requested while already running")
        return {"ok": False, "error": "Compute already running", "status": ComputeStatus.RUNNING.value}

    state.compute = ComputeState(status=ComputeStatus.RUNNING, message="Computing...")

    state.compute_thread = threading.Thread(
        target=_run_compute_job, args=(state,), name="vime-compute", daemon=True
    )
    state.compute_thread.start()
    logger.info("Compute thread started")
    return {"ok": True, "status": state.compute.status.value, "message": state.compute.message}


def handle_status(state, _payload):
    """Return the current compute job status."""
    logger.debug("Compute status requested: %s", state.compute.status.value)
    return {
        "ok": True,
        "status": state.compute.status.value,
        "message": state.compute.message,
        "table": state.compute.table_name,
        "error": state.compute.error,
    }


def _run_compute_job(state):
    """Execute the compute job in a background thread."""
    try:
        logger.info("Compute job started")
        df = test_compute()
        name = _new_compute_name(state)
        state.virtual_tables[name] = {
            "name": name,
            "df": df,
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
        }
        state.compute = ComputeState(
            status=ComputeStatus.DONE,
            message=f"Compute done: {name}",
            table_name=name,
        )
        logger.info("Compute job completed: %s", name)
    except Exception as exc:
        state.compute = ComputeState(
            status=ComputeStatus.ERROR,
            message="Compute failed",
            error=str(exc),
        )
        logger.exception("Compute job failed")


def _new_compute_name(state):
    """Generate a unique name for a new virtual table."""
    base = "/__computed__/compute"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{base}_{stamp}"
    if name not in state.virtual_tables:
        return name
    suffix = 1
    while f"{name}_{suffix}" in state.virtual_tables:
        suffix += 1
    return f"{name}_{suffix}"
