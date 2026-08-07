"""Plot command handler — async start/status pattern."""

import logging
import threading

import numpy as np

from plotter import VALID_CHARSETS, VALID_PLOT_TYPES, render_plot
from server.commands.table import _apply_column_config, _filtered_table
from server.state import JobState, JobStatus

logger = logging.getLogger("vime")

MAX_GROUPS = 8
VALID_AGGS = frozenset({"mean", "sum", "count", "median"})
VALID_SCALES = frozenset({"linear", "log"})


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
    """Core plot generation: prep series, then render."""
    name = payload.get("dataset", payload.get("name"))
    if not name:
        return {"ok": False, "error": "No dataset specified"}

    cols = payload.get("columns", payload.get("cols", []))
    plot_type = str(payload.get("type", "line")).lower()
    if plot_type not in VALID_PLOT_TYPES:
        return {
            "ok": False,
            "error": "Unknown plot type {!r}. Use: {}".format(
                plot_type, ", ".join(sorted(VALID_PLOT_TYPES))
            ),
        }

    width = int(payload.get("width", 72))
    height = int(payload.get("height", 20))
    charset = payload.get("charset", "braille")
    if charset not in VALID_CHARSETS:
        charset = "braille"

    x_scale = str(payload.get("x_scale", "linear")).lower()
    y_scale = str(payload.get("y_scale", "linear")).lower()
    if x_scale not in VALID_SCALES or y_scale not in VALID_SCALES:
        return {"ok": False, "error": "scale must be linear or log"}

    try:
        x_lim = _parse_lim(payload.get("x_lim"))
        y_lim = _parse_lim(payload.get("y_lim"))
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    sort_x = bool(payload.get("sort_x", False))
    groupby = payload.get("groupby")
    agg = str(payload.get("agg", "mean")).lower()
    if groupby and agg not in VALID_AGGS:
        return {"ok": False, "error": "agg must be one of: " + ", ".join(sorted(VALID_AGGS))}

    if plot_type == "hist":
        if len(cols) < 1:
            return {"ok": False, "error": "Need at least 1 column for hist"}
    elif len(cols) < 2:
        return {"ok": False, "error": "Need at least 2 column indices (x y)"}

    session.ensure_open()
    try:
        df = _load_plot_frame(session, name, cols, payload.get("filter"), groupby)
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid filter: {exc}"}
    if df is None:
        return {"ok": False, "error": f"Table not found: {name}"}

    # Config order only when caller did not pin an explicit column list of names.
    if not payload.get("columns"):
        df = _apply_column_config(session, name, df)

    try:
        if plot_type == "hist":
            y_col = _resolve_column(df, cols[0] if len(cols) == 1 else cols[1])
            x_col = y_col
        else:
            x_col = _resolve_column(df, cols[0])
            y_col = _resolve_column(df, cols[1])
        group_col = _resolve_column(df, groupby) if groupby else None
    except (IndexError, KeyError) as exc:
        logger.warning("Invalid plot column: %s", exc)
        return {"ok": False, "error": f"Invalid column: {exc}"}

    try:
        series = _build_series(df, x_col, y_col, group_col, agg, plot_type)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}

    if not series or all(len(s["x"]) == 0 for s in series):
        return {"ok": False, "error": "No valid data points to plot"}

    try:
        plot_lines = render_plot(
            series,
            width=width,
            height=height,
            plot_type=plot_type,
            x_label=str(x_col) if plot_type != "hist" else str(y_col),
            y_label="count" if plot_type == "hist" else str(y_col),
            charset=charset,
            x_scale=x_scale,
            y_scale=y_scale,
            x_lim=x_lim,
            y_lim=y_lim,
            sort_x=sort_x,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    meta = [plot_type, charset]
    if x_scale != "linear" or y_scale != "linear":
        meta.append("x=" + x_scale + "/y=" + y_scale)
    if group_col is not None:
        meta.append("group=" + str(group_col) + "/" + agg)
    title = "Plot: {} vs {}  ({})  [{}]".format(
        x_col, y_col, name, " ".join(meta)
    )
    if plot_type == "hist":
        title = "Plot: hist({})  ({})  [{}]".format(y_col, name, " ".join(meta))
    content = title + "\n\n" + "\n".join(plot_lines)
    logger.debug("Plot generated: %s (%d series)", title, len(series))
    return {"ok": True, "content": content}


def _load_plot_frame(session, name, cols, filter_spec, groupby):
    """Load only columns needed for the plot (plus filter/groupby columns)."""
    needed = list(cols)
    if groupby is not None:
        needed.append(groupby)
    if filter_spec and isinstance(filter_spec, dict):
        needed.append(filter_spec.get("left"))
        right = filter_spec.get("right") or {}
        if right.get("kind") == "column":
            needed.append(right.get("value"))
    # Names may be indices; resolve after load when filter forces full frame.
    if filter_spec is not None:
        return _filtered_table(session, name, filter_spec)

    column_names = [c for c in needed if c is not None and not isinstance(c, int)]
    # Also keep stringified ints that look like names — load all then select by resolve.
    df = session.load_table(name, columns=column_names or None)
    if df is None:
        return None
    if column_names:
        # Indices still need full columns; if any col ref is int, reload full.
        if any(isinstance(c, int) for c in needed if c is not None):
            df = session.load_table(name)
    return df


def _build_series(df, x_col, y_col, group_col, agg, plot_type):
    if group_col is None or plot_type == "hist":
        x = _to_float(df[x_col], x_col)
        y = _to_float(df[y_col], y_col)
        return [{"label": "", "x": x, "y": y}]

    groups = list(df.groupby(group_col, sort=False))
    overflow = max(0, len(groups) - MAX_GROUPS)
    groups = groups[:MAX_GROUPS]
    series = []
    for key, frame in groups:
        label = str(key)
        if plot_type == "bar":
            # Aggregate y by x categories for bar groupby.
            grouped = frame.groupby(x_col, sort=False)[y_col]
            if agg == "count":
                reduced = grouped.count()
            else:
                numeric = frame[[x_col, y_col]].copy()
                numeric[y_col] = _to_float(numeric[y_col], y_col)
                grouped = numeric.groupby(x_col, sort=False)[y_col]
                reduced = getattr(grouped, agg)()
            x = np.arange(len(reduced), dtype=float)
            y = reduced.to_numpy(dtype=float)
            # Encode category positions; labels stay in series label.
            series.append({"label": label, "x": x, "y": y})
        else:
            x = _to_float(frame[x_col], x_col)
            y = _to_float(frame[y_col], y_col)
            if agg != "mean" and plot_type in ("line", "scatter"):
                # For line/scatter, groupby splits series; agg unused unless bar.
                pass
            series.append({"label": label, "x": x, "y": y})
    if overflow:
        series.append({
            "label": "+{} more".format(overflow),
            "x": np.array([], dtype=float),
            "y": np.array([], dtype=float),
        })
    return [s for s in series if len(s["x"]) or s["label"].startswith("+")]


def _to_float(series, name):
    try:
        values = np.asarray(series, dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError("Cannot convert column {!r} to numeric: {}".format(name, exc)) from exc
    return values


def _parse_lim(value):
    if value is None or value == "auto":
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [float(value[0]), float(value[1])]
    raise ValueError("lim must be [min, max] or auto")


def _resolve_column(df, ref):
    """Resolve a column reference (int index or string name)."""
    if isinstance(ref, int):
        if ref < 0 or ref >= len(df.columns):
            raise IndexError(f"Column index {ref} out of range (0-{len(df.columns)-1})")
        return df.columns[ref]
    if ref in df.columns:
        return ref
    by_name = {str(col): col for col in df.columns}
    if str(ref) in by_name:
        return by_name[str(ref)]
    try:
        idx = int(ref)
        if 0 <= idx < len(df.columns):
            return df.columns[idx]
    except (ValueError, TypeError):
        pass
    raise KeyError(ref)
