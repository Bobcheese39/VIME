"""Info command handler."""

import logging

import numpy as np
from tabulate import tabulate

logger = logging.getLogger("vime")


def handle(state, payload):
    """Return detailed info about a table."""
    if not state.loader.is_open:
        logger.warning("Info requested with no file open")
        return {"ok": False, "error": "No file open"}

    name = payload.get("name", "")
    logger.debug("Info requested for table: %s", name)

    df = state.load_table(name)
    if df is None:
        logger.warning("Info table not found: %s", name)
        return {"ok": False, "error": f"Table not found: {name}"}

    lines = []
    lines.append(f"Table: {name}")
    lines.append(f"Shape: {df.shape[0]} rows x {df.shape[1]} columns")
    lines.append("")
    lines.append("Columns:")
    lines.append("\u2500" * 50)
    for i, col in enumerate(df.columns):
        dtype = df[col].dtype
        non_null = df[col].count()
        lines.append(f"  {i:>3}  {str(col):<30} {str(dtype):<12} ({non_null} non-null)")
    lines.append("\u2500" * 50)

    # Numeric summary
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        lines.append("")
        lines.append("Numeric Summary:")
        desc = df[numeric_cols].describe().T
        lines.append(
            tabulate(
                desc,
                headers="keys",
                tablefmt="plain",
                stralign="left",
                numalign="left",
            )
        )

    return {"ok": True, "content": "\n".join(lines)}
