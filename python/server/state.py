"""Shared server state, compute types, and common helpers."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from data_loader import DataLoader
from config import Config

logger = logging.getLogger("vime")


class ComputeStatus(Enum):
    """Possible states for a background compute job."""
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class ComputeState:
    """Snapshot of a background compute job's progress."""
    status: ComputeStatus = ComputeStatus.IDLE
    message: str = ""
    table_name: Optional[str] = None
    error: Optional[str] = None


class ServerState:
    """Shared state that holds H5 data and is passed to command handlers."""

    def __init__(self):
        self.loader = DataLoader()
        self.current_df = None     # Last-fetched DataFrame
        self.current_table = None  # Name of the last-fetched table
        self.virtual_tables = {}   # Virtual tables created by compute jobs
        self.compute_thread = None
        self.compute = ComputeState()
        self.config = None
        try:
            self.config = Config()
            logger.info("Table config initialized")
        except Exception as exc:
            logger.warning("Table config disabled: %s", exc)

    def close_handles(self):
        """Close any open file handles."""
        logger.info("Closing open file handles")
        self.loader.close()

    def load_table(self, name):
        """Load a table/dataset as a DataFrame from either backend.

        Returns:
            DataFrame if successful, None if not found.
        """
        if name in self.virtual_tables:
            logger.debug("Loading virtual table: %s", name)
            return self.virtual_tables[name]["df"]
        logger.debug("Loading table from store: %s", name)
        return self.loader.load_table(name)

    def get_table_list(self):
        """Return a list of dicts with table metadata."""
        tables = list(self.loader.list_tables())
        if self.virtual_tables:
            logger.debug("Adding %d virtual tables", len(self.virtual_tables))
            tables.extend(
                {
                    "name": entry["name"],
                    "rows": entry["rows"],
                    "cols": entry["cols"],
                }
                for entry in self.virtual_tables.values()
            )
        return tables
