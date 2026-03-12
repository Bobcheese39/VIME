"""Plot command handler — async start/status pattern."""

import logging
import threading

import numpy as np
from plotter import braille_plot
from server.state import PlotState, JobStatus

logger = logging.getLogger("vime")


def handle(state, payload):
    """Synchronous plot (kept for backward compatibility)."""
    return _generate_plot(state, payload)


def handle_start(state, payload):
    """Validate inputs, then spawn a background thread for plot generation."""
    if state.plot_thread is not None and state.plot_thread.is_alive():
        logger.warning("Plot start requested while already running")
        return {"ok": False, "error": "Plot already running", "status": JobStatus.RUNNING.value}

    validation = _validate_plot_inputs(state, payload)
    if validation is not None:
        return validation

    state.plot = PlotState(status=JobStatus.RUNNING, message="Generating plot...")

    state.plot_thread = threading.Thread(
        target=_run_plot_job, args=(state, payload), name="vime-plot", daemon=True
    )
    state.plot_thread.start()
    logger.info("Plot thread started")
    return {"ok": True, "status": state.plot.status.value, "message": state.plot.message}


def handle_status(state, _payload):
    """Return the current plot job status."""
    logger.debug("Plot status requested: %s", state.plot.status.value)
    resp = {
        "ok": True,
        "status": state.plot.status.value,
        "message": state.plot.message,
        "error": state.plot.error,
    }
    if state.plot.status == JobStatus.DONE:
        resp["content"] = state.plot.content
    return resp


def _validate_plot_inputs(state, payload):
    """Validate plot inputs synchronously. Returns an error dict, or None if valid."""
    if state.current_df is None:
        logger.warning("Plot requested with no table loaded")
        return {"ok": False, "error": "No table loaded. Open a table first."}

    cols = payload.get("cols", [])
    if len(cols) < 2:
        return {"ok": False, "error": "Need at least 2 column indices (x y)"}

    df = state.current_df
    try:
        x_col = _resolve_column(df, cols[0])
        y_col = _resolve_column(df, cols[1])
    except (IndexError, KeyError) as exc:
        logger.warning("Invalid plot column: %s", exc)
        return {"ok": False, "error": f"Invalid column: {exc}"}

    try:
        df[x_col].values.astype(float)
    except (ValueError, TypeError) as exc:
        logger.warning("Non-numeric x column %s: %s", x_col, exc)
        return {"ok": False, "error": f"Cannot convert column '{x_col}' to numeric: {exc}"}

    try:
        df[y_col].values.astype(float)
    except (ValueError, TypeError) as exc:
        logger.warning("Non-numeric y column %s: %s", y_col, exc)
        return {"ok": False, "error": f"Cannot convert column '{y_col}' to numeric: {exc}"}

    return None


def _run_plot_job(state, payload):
    """Execute plot generation in a background thread."""
    try:
        logger.info("Plot job started")
        result = _generate_plot(state, payload)
        if result.get("ok"):
            state.plot = PlotState(
                status=JobStatus.DONE,
                message="Plot done",
                content=result["content"],
            )
            logger.info("Plot job completed")
        else:
            state.plot = PlotState(
                status=JobStatus.ERROR,
                message="Plot failed",
                error=result.get("error", "Unknown error"),
            )
            logger.warning("Plot job returned error: %s", result.get("error"))
    except Exception as exc:
        state.plot = PlotState(
            status=JobStatus.ERROR,
            message="Plot failed",
            error=str(exc),
        )
        logger.exception("Plot job failed")


def _generate_plot(state, payload):
    """Core plot generation logic (runs in any context)."""
    if state.current_df is None:
        return {"ok": False, "error": "No table loaded. Open a table first."}

    cols = payload.get("cols", [])
    plot_type = payload.get("type", "line")
    width = payload.get("width", 72)
    height = payload.get("height", 20)
    logger.debug("Plot request: cols=%s type=%s size=%sx%s", cols, plot_type, width, height)

    if len(cols) < 2:
        return {"ok": False, "error": "Need at least 2 column indices (x y)"}

    df = state.current_df

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
    title = f"Plot: {x_col} vs {y_col}  ({state.current_table})"
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
