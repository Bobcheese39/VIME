"""Shared server state, per-file sessions, compute types, and common helpers."""

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from data_loader import DataLoader
from config import Config

logger = logging.getLogger("vime")


class JobStatus(Enum):
    """Possible states for a background job (compute, plot, etc.)."""
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobState:
    """Snapshot of a background job's progress (compute, plot, etc.).

    ``result`` carries the job's payload: the plot content or the new virtual
    table's name, depending on the job.
    """
    status: JobStatus = JobStatus.IDLE
    message: str = ""
    result: Optional[str] = None
    error: Optional[str] = None


def normalize_path(path):
    """Normalize a path for use as a session key and same-file comparisons."""
    return os.path.normcase(os.path.abspath(path or ""))


class Session:
    """Per-file state: a data handle plus its current table, virtual tables,
    and any in-flight compute/plot jobs.

    Sessions are keyed by normalized file path so multiple TUI runs can share
    a single daemon without clobbering each other's state.
    """

    def __init__(self, filepath, config=None):
        self.filepath = filepath        # Original (un-normalized) file path
        self.config = config            # Shared column-order config
        self.loader = DataLoader()
        self.virtual_tables = {}        # Virtual tables created by compute jobs
        self.filtered_cache = None       # One ((dataset, filter), DataFrame) entry
        self.compute_thread = None
        self.compute = JobState()
        self.plot_thread = None
        self.plot = JobState()

    def ensure_open(self):
        """Reopen the file handle if it was closed (e.g. by LRU eviction)."""
        if not self.loader.is_open and self.filepath:
            logger.info("Re-opening evicted/closed file: %s", self.filepath)
            self.loader.open(self.filepath)

    def close(self):
        """Close any open file handles for this session."""
        logger.info("Closing file handles for session: %s", self.filepath)
        self.loader.close()

    def load_table(self, name, columns=None):
        """Load a table/dataset as a DataFrame from either backend.

        Returns:
            DataFrame if successful, None if not found.
        """
        if name in self.virtual_tables:
            logger.debug("Loading virtual table: %s", name)
            return DataLoader._select_columns(self.virtual_tables[name]["df"], columns)
        logger.debug("Loading table from store: %s", name)
        return self.loader.load_table(name, columns=columns)

    def load_table_slice(self, name, start, stop, columns=None):
        """Load a bounded row slice from a stored or virtual table."""
        if name in self.virtual_tables:
            df = self.virtual_tables[name]["df"].iloc[start:stop]
            return DataLoader._select_columns(df, columns)
        return self.loader.load_table_slice(name, start, stop, columns)

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


class ServerState:
    """Manages per-file sessions, shared config, and idle-activity tracking."""

    def __init__(self, max_sessions=None):
        if max_sessions is None:
            max_sessions = _safe_int(os.environ.get("VIME_MAX_SESSIONS", "16"), 16)
        self.max_sessions = max_sessions
        self.sessions = OrderedDict()   # normalized path -> Session (LRU order)
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()
        self.config = None
        try:
            self.config = Config()
            logger.info("Table config initialized")
        except Exception as exc:
            logger.warning("Table config disabled: %s", exc)

    def mark_activity(self):
        """Record that a request was just handled (resets the idle timer)."""
        self.last_activity = time.monotonic()

    def get_session(self, filepath, create=False):
        """Return the Session for *filepath*, optionally creating it.

        Creating a session may evict the least-recently-used session (closing
        its file handles) when over ``max_sessions``. Accessing a session moves
        it to the most-recently-used position.
        """
        if not filepath:
            return None
        key = normalize_path(filepath)
        with self.lock:
            session = self.sessions.get(key)
            if session is not None:
                self.sessions.move_to_end(key)
                return session
            if not create:
                return None
            session = Session(filepath, config=self.config)
            self.sessions[key] = session
            self._evict_if_needed()
            return session

    def session_for(self, payload, create=False):
        """Resolve the session referenced by a request payload's 'file'."""
        return self.get_session(payload.get("file", ""), create=create)

    def _evict_if_needed(self):
        """Close and drop least-recently-used sessions beyond the cap.

        Caller must hold ``self.lock``.
        """
        while len(self.sessions) > self.max_sessions:
            old_key, old_session = self.sessions.popitem(last=False)
            logger.info("Evicting LRU session: %s", old_key)
            try:
                old_session.close()
            except Exception as exc:
                logger.warning("Failed to close evicted session %s: %s", old_key, exc)

    def close_handles(self):
        """Close all open file handles across all sessions."""
        logger.info("Closing all session file handles")
        with self.lock:
            for session in self.sessions.values():
                try:
                    session.close()
                except Exception as exc:
                    logger.warning("Failed to close session %s: %s", session.filepath, exc)


def _safe_int(value, default, minimum=1):
    """Parse *value* as int; return *default* when invalid or below *minimum*."""
    try:
        n = int(value)
        return n if n >= minimum else default
    except (ValueError, TypeError):
        return default
