"""Background compute command handlers."""

import logging
import time
import threading

from server.state import ComputeState, ComputeStatus

logger = logging.getLogger("vime")


def handle_start(state, payload):
    """Start a background compute job using test_compute()."""
    session = state.session_for(payload)
    if session is None:
        return {"ok": False, "error": "No file open"}

    if session.compute_thread is not None and session.compute_thread.is_alive():
        logger.warning("Compute start requested while already running")
        return {"ok": False, "error": "Compute already running", "status": ComputeStatus.RUNNING.value}

    session.compute = ComputeState(status=ComputeStatus.RUNNING, message="Computing...")

    session.compute_thread = threading.Thread(
        target=_run_compute_job, args=(session,), name="vime-compute", daemon=True
    )
    session.compute_thread.start()
    logger.info("Compute thread started")
    return {"ok": True, "status": session.compute.status.value, "message": session.compute.message}


def handle_status(state, payload):
    """Return the current compute job status."""
    session = state.session_for(payload)
    if session is None:
        return {"ok": False, "error": "No file open"}
    logger.debug("Compute status requested: %s", session.compute.status.value)
    return {
        "ok": True,
        "status": session.compute.status.value,
        "message": session.compute.message,
        "table": session.compute.table_name,
        "error": session.compute.error,
    }


def _run_compute_job(session):
    """Execute the compute job in a background thread."""
    try:
        logger.info("Compute job started")
        from test_compute import test_compute

        df = test_compute()
        name = _new_compute_name(session)
        session.virtual_tables[name] = {
            "name": name,
            "df": df,
            "rows": int(df.shape[0]),
            "cols": int(df.shape[1]),
        }
        session.compute = ComputeState(
            status=ComputeStatus.DONE,
            message=f"Compute done: {name}",
            table_name=name,
        )
        logger.info("Compute job completed: %s", name)
    except Exception as exc:
        session.compute = ComputeState(
            status=ComputeStatus.ERROR,
            message="Compute failed",
            error=str(exc),
        )
        logger.exception("Compute job failed")


def _new_compute_name(session):
    """Generate a unique name for a new virtual table."""
    base = "/__computed__/compute"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{base}_{stamp}"
    if name not in session.virtual_tables:
        return name
    suffix = 1
    while f"{name}_{suffix}" in session.virtual_tables:
        suffix += 1
    return f"{name}_{suffix}"
