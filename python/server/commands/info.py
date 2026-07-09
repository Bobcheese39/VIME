"""Info command handler."""

import logging

logger = logging.getLogger("vime")


def handle(state, payload):
    """Return detailed info about a table."""
    session = state.session_for(payload)
    if session is None:
        logger.warning("Info requested with no file open")
        return {"ok": False, "error": "No file open"}

    try:
        session.ensure_open()
    except Exception as exc:
        logger.warning("Failed to reopen session %s: %s", session.filepath, exc)
        return {"ok": False, "error": str(exc)}

    name = payload.get("name", "")
    logger.debug("Info requested for table: %s", name)

    df = session.load_table(name)
    if df is None:
        logger.warning("Info table not found: %s", name)
        return {"ok": False, "error": f"Table not found: {name}"}

    import numpy as np
    from tabulate import tabulate

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
