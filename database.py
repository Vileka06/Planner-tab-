"""SQLite persistence layer for the GitHub Contribution Planner.

This module is the ONLY place in the project that talks to SQLite. Keeping
every SQL statement here means the UI and the business logic never need to
know how data is stored, which makes the app easier to test and to change.

It deliberately returns plain Python dictionaries (not sqlite3.Row objects)
so that callers can treat the data as normal dicts.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# The database file lives next to this module (project root).
DB_PATH = Path(__file__).resolve().parent / "planner.db"

# Allowed values keep the data predictable. Anything outside these lists is
# rejected by the functions below instead of silently corrupting the table.
GOAL_STATUSES = ("active", "completed")


@contextmanager
def _connect(db_path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close.

    Using a context manager guarantees the file handle is released even if a
    query raises, which avoids locked-database problems on repeated runs.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Enforce the foreign key between sessions and goals.
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_iso() -> str:
    """Timestamp helper so every row records when it was created (UTC)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create tables if they do not exist. Safe to call on every app start."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS goals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                title          TEXT    NOT NULL,
                description    TEXT    NOT NULL DEFAULT '',
                target_date    TEXT,
                target_minutes INTEGER NOT NULL DEFAULT 0,
                status         TEXT    NOT NULL DEFAULT 'active',
                created_at     TEXT    NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id      INTEGER,
                session_date TEXT    NOT NULL,
                minutes      INTEGER NOT NULL,
                notes        TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals (id) ON DELETE SET NULL
            );
            """
        )
        # Indexes keep date-based lookups (weekly/monthly charts) fast.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions (session_date);"
        )


# --------------------------------------------------------------------------- #
# Goal operations
# --------------------------------------------------------------------------- #
def add_goal(
    title: str,
    description: str = "",
    target_date: Optional[str] = None,
    target_minutes: int = 0,
    db_path: Path | str = DB_PATH,
) -> int:
    """Insert a goal and return its new id. Validates the inputs first."""
    title = (title or "").strip()
    if not title:
        raise ValueError("A goal needs a non-empty title.")
    target_minutes = int(target_minutes or 0)
    if target_minutes < 0:
        raise ValueError("Target minutes cannot be negative.")

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO goals (title, description, target_date, target_minutes,
                               status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?);
            """,
            (title, (description or "").strip(), target_date, target_minutes, _now_iso()),
        )
        return int(cursor.lastrowid)


def get_goals(
    status: Optional[str] = None,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    """Return goals, newest first. Optionally filter by status."""
    query = "SELECT * FROM goals"
    params: tuple[Any, ...] = ()
    if status is not None:
        if status not in GOAL_STATUSES:
            raise ValueError(f"status must be one of {GOAL_STATUSES}")
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY datetime(created_at) DESC, id DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_goal(goal_id: int, db_path: Path | str = DB_PATH) -> Optional[dict[str, Any]]:
    """Return a single goal as a dict, or None if it does not exist."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM goals WHERE id = ?;", (goal_id,)).fetchone()
    return dict(row) if row else None


def set_goal_status(
    goal_id: int,
    status: str,
    db_path: Path | str = DB_PATH,
) -> bool:
    """Mark a goal 'active' or 'completed'. Returns True if a row changed."""
    if status not in GOAL_STATUSES:
        raise ValueError(f"status must be one of {GOAL_STATUSES}")
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE goals SET status = ? WHERE id = ?;", (status, goal_id)
        )
        return cursor.rowcount > 0


def delete_goal(goal_id: int, db_path: Path | str = DB_PATH) -> bool:
    """Delete a goal. Its sessions are kept but become unlinked (goal_id=NULL)."""
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM goals WHERE id = ?;", (goal_id,))
        return cursor.rowcount > 0


# --------------------------------------------------------------------------- #
# Session operations
# --------------------------------------------------------------------------- #
def add_session(
    session_date: str,
    minutes: int,
    goal_id: Optional[int] = None,
    notes: str = "",
    db_path: Path | str = DB_PATH,
) -> int:
    """Record a coding session and return its id."""
    if not session_date:
        raise ValueError("A session needs a date (YYYY-MM-DD).")
    minutes = int(minutes)
    if minutes <= 0:
        raise ValueError("Session minutes must be greater than zero.")

    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO sessions (goal_id, session_date, minutes, notes, created_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (goal_id, session_date, minutes, (notes or "").strip(), _now_iso()),
        )
        return int(cursor.lastrowid)


def get_sessions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    goal_id: Optional[int] = None,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    """Return sessions, newest first, with optional date and goal filters.

    Dates are ISO strings (YYYY-MM-DD) and compared lexicographically, which
    is correct for this format.
    """
    query = "SELECT * FROM sessions WHERE 1 = 1"
    params: list[Any] = []
    if start_date:
        query += " AND session_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND session_date <= ?"
        params.append(end_date)
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    query += " ORDER BY session_date DESC, id DESC"

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_sessions_for_goal(
    goal_id: int, db_path: Path | str = DB_PATH
) -> list[dict[str, Any]]:
    """Convenience wrapper used by the goal tracker."""
    return get_sessions(goal_id=goal_id, db_path=db_path)


def delete_session(session_id: int, db_path: Path | str = DB_PATH) -> bool:
    """Delete a single session."""
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM sessions WHERE id = ?;", (session_id,))
        return cursor.rowcount > 0
