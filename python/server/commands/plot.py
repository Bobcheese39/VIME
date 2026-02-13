"""Plot command handler."""

import logging

import numpy as np
from plotter import braille_plot

logger = logging.getLogger("vime")


def handle(state, payload):
    """Generate a braille Unicode plot from the current table."""
    if state.current_df is None:
        logger.warning("Plot requested with no table loaded")
        return {"ok": False, "error": "No table loaded. Open a table first."}

    cols = payload.get("cols", [])
    plot_type = payload.get("type", "line")
    width = payload.get("width", 72)
    height = payload.get("height", 20)
    logger.debug("Plot request: cols=%s type=%s size=%sx%s", cols, plot_type, width, height)

    if len(cols) < 2:
        return {"ok": False, "error": "Need at least 2 column indices (x y)"}

    df = state.current_df

    # Resolve column references (index or name)
    try:
        x_col = _resolve_column(df, cols[0])
        y_col = _resolve_column(df, cols[1])
    except (IndexError, KeyError) as exc:
        logger.warning("Invalid plot column: %s", exc)
        return {"ok": False, "error": f"Invalid column: {exc}"}

    # Convert to float with error handling for non-numeric data
    try:
        x = df[x_col].values.astype(float)
    except (ValueError, TypeError) as exc:
        logger.warning("Non-numeric x column %s: %s", x_col, exc)
        return {"ok": False, "error": f"Cannot convert column '{x_col}' to numeric: {exc}"}

    try:
        y = df[y_col].values.astype(float)
    except (ValueError, TypeError) as exc:
        logger.warning("Non-numeric y column %s: %s", y_col, exc)
        return {"ok": False, "error": f"Cannot convert column '{y_col}' to numeric: {exc}"}

    # Remove NaN pairs
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) == 0:
        logger.warning("No valid data points after NaN filtering")
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
    # Try as string name
    if ref in df.columns:
        return ref
    # Try parsing as int
    try:
        idx = int(ref)
        if 0 <= idx < len(df.columns):
            return df.columns[idx]
    except (ValueError, TypeError):
        pass
    raise KeyError(ref)
