"""Plot command handler — async start/status pattern."""

import logging
import threading

from server.state import JobState, JobStatus

logger = logging.getLogger("vime")


def handle_start(state, payload):
    """Spawn a background thread for plot generation."""
    session = state.session_for(payload)
    if session is None:
        return {"ok": False, "error": "No table loaded. Open a table first."}

    if session.plot_thread is not None and session.plot_thread.is_alive():
        logger.warning("Plot start requested while already running")
        return {"ok": False, "error": "Plot already running", "status": JobStatus.RUNNING.value}

    session.plot = JobState(status=JobStatus.RUNNING, message="Generating plot...")

    session.plot_thread = threading.Thread(
        target=_run_plot_job, args=(session, payload), name="vime-plot", daemon=True
    )
    session.plot_thread.start()
    logger.info("Plot thread started")
    return {"ok": True, "status": session.plot.status.value, "message": session.plot.message}


def handle_status(state, payload):
    """Return the current plot job status."""
    session = state.session_for(payload)
    if session is None:
        return {"ok": False, "error": "No table loaded. Open a table first."}
    logger.debug("Plot status requested: %s", session.plot.status.value)
    resp = {
        "ok": True,
        "status": session.plot.status.value,
        "message": session.plot.message,
        "error": session.plot.error,
    }
    if session.plot.status == JobStatus.DONE:
        resp["content"] = session.plot.result
    return resp


def _run_plot_job(session, payload):
    """Execute plot generation in a background thread."""
    try:
        logger.info("Plot job started")
        result = _generate_plot(session, payload)
        if result.get("ok"):
            session.plot = JobState(
                status=JobStatus.DONE,
                message="Plot done",
                result=result["content"],
            )
            logger.info("Plot job completed")
        else:
            session.plot = JobState(
                status=JobStatus.ERROR,
                message="Plot failed",
                error=result.get("error", "Unknown error"),
            )
            logger.warning("Plot job returned error: %s", result.get("error"))
    except Exception as exc:
        session.plot = JobState(
            status=JobStatus.ERROR,
            message="Plot failed",
            error=str(exc),
        )
        logger.exception("Plot job failed")


def _generate_plot(session, payload):
    """Core plot generation logic (runs in any context)."""
    if session.current_df is None:
        return {"ok": False, "error": "No table loaded. Open a table first."}

    import numpy as np
    from plotter import braille_plot

    cols = payload.get("cols", [])
    plot_type = payload.get("type", "line")
    width = payload.get("width", 72)
    height = payload.get("height", 20)
    logger.debug("Plot request: cols=%s type=%s size=%sx%s", cols, plot_type, width, height)

    if len(cols) < 2:
        return {"ok": False, "error": "Need at least 2 column indices (x y)"}

    df = session.current_df

    try:
        x_col = _resolve_column(df, cols[0])
        y_col = _resolve_column(df, cols[1])
    except (IndexError, KeyError) as exc:
        logger.warning("Invalid plot column: %s", exc)
        return {"ok": False, "error": f"Invalid column: {exc}"}

    try:
        x = df[x_col].values.astype(float)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Cannot convert column '{x_col}' to numeric: {exc}"}

    try:
        y = df[y_col].values.astype(float)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Cannot convert column '{y_col}' to numeric: {exc}"}

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) == 0:
        return {"ok": False, "error": "No valid data points to plot"}

    plot_lines = braille_plot(x, y, width=width, height=height,
                              x_label=str(x_col), y_label=str(y_col),
                              plot_type=plot_type)
    title = f"Plot: {x_col} vs {y_col}  ({session.current_table})"
    content = title + "\n\n" + "\n".join(plot_lines)
    logger.debug("Plot generated: %s vs %s (%d points)", x_col, y_col, len(x))
    return {"ok": True, "content": content}


def _resolve_column(df, ref):
    """Resolve a column reference (int index or string name)."""
    if isinstance(ref, int):
        if ref < 0 or ref >= len(df.columns):
            raise IndexError(f"Column index {ref} out of range (0-{len(df.columns)-1})")
        return df.columns[ref]
    if ref in df.columns:
        return ref
    try:
        idx = int(ref)
        if 0 <= idx < len(df.columns):
            return df.columns[idx]
    except (ValueError, TypeError):
        pass
    raise KeyError(ref)
