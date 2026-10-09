"""Business logic for the daily coding planner.

This layer sits between the UI (``app.py``) and storage (``database.py``). It
adds validation and the progress/streak/calendar calculations that the
dashboard needs, but it never runs SQL itself and never imports Streamlit.

All progress is calculated only from sessions the user actually recorded. No
estimates or placeholder activity are generated.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

import database as db


class PlannerError(Exception):
    """Raised for invalid planner input (bad dates, negative minutes, etc.)."""


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _validate_minutes(minutes: Any) -> int:
    try:
        value = int(minutes)
    except (TypeError, ValueError) as exc:
        raise PlannerError("Minutes must be a whole number.") from exc
    if value <= 0:
        raise PlannerError("Minutes must be greater than zero.")
    if value > 24 * 60:
        raise PlannerError("A single session cannot exceed 24 hours.")
    return value


def _validate_date(value: str, field: str = "date") -> str:
    if not value:
        raise PlannerError(f"A {field} is required.")
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise PlannerError(f"The {field} must be in YYYY-MM-DD format.") from exc


# --------------------------------------------------------------------------- #
# Goals
# --------------------------------------------------------------------------- #
def create_goal(
    title: str,
    description: str = "",
    target_date: Optional[str] = None,
    target_minutes: int = 0,
) -> int:
    """Validate and store a new goal, returning its id."""
    title = (title or "").strip()
    if not title:
        raise PlannerError("Please give the goal a title.")
    if len(title) > 120:
        raise PlannerError("Keep the goal title under 120 characters.")
    if target_date:
        target_date = _validate_date(target_date, "target date")
    try:
        target_minutes = int(target_minutes or 0)
    except (TypeError, ValueError) as exc:
        raise PlannerError("Target minutes must be a whole number.") from exc
    if target_minutes < 0:
        raise PlannerError("Target minutes cannot be negative.")
    return db.add_goal(title, description, target_date, target_minutes)


def list_goals(status: Optional[str] = None) -> list[dict[str, Any]]:
    """Return goals from storage."""
    return db.get_goals(status=status)


def mark_goal_complete(goal_id: int) -> bool:
    return db.set_goal_status(goal_id, "completed")


def reopen_goal(goal_id: int) -> bool:
    return db.set_goal_status(goal_id, "active")


def remove_goal(goal_id: int) -> bool:
    return db.delete_goal(goal_id)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def log_session(
    session_date: str,
    minutes: int,
    goal_id: Optional[int] = None,
    notes: str = "",
) -> int:
    """Validate and store a coding session, returning its id."""
    session_date = _validate_date(session_date, "session date")
    if date.fromisoformat(session_date) > date.today() + timedelta(days=1):
        raise PlannerError("You cannot log a session far in the future.")
    minutes = _validate_minutes(minutes)
    if goal_id is not None and db.get_goal(goal_id) is None:
        raise PlannerError("The selected goal no longer exists.")
    return db.add_session(session_date, minutes, goal_id, notes)


def remove_session(session_id: int) -> bool:
    return db.delete_session(session_id)


def sessions_dataframe(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> pd.DataFrame:
    """Return recorded sessions as a DataFrame with a parsed date column."""
    rows = db.get_sessions(start_date=start_date, end_date=end_date)
    columns = ["id", "goal_id", "session_date", "minutes", "notes", "created_at"]
    if not rows:
        return pd.DataFrame(columns=columns + ["date"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["session_date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- #
# Progress analytics
# --------------------------------------------------------------------------- #
def progress_by_goal() -> pd.DataFrame:
    """One row per goal with logged minutes and completion percentage."""
    goals = db.get_goals()
    columns = [
        "id", "title", "status", "target_minutes", "logged_minutes", "percent",
        "session_count", "target_date",
    ]
    if not goals:
        return pd.DataFrame(columns=columns)

    records: list[dict[str, Any]] = []
    for goal in goals:
        sessions = db.get_sessions_for_goal(goal["id"])
        logged = sum(s["minutes"] for s in sessions)
        target = goal["target_minutes"] or 0
        percent = round(logged / target * 100, 1) if target > 0 else None
        records.append(
            {
                "id": goal["id"],
                "title": goal["title"],
                "status": goal["status"],
                "target_minutes": target,
                "logged_minutes": logged,
                "percent": percent,
                "session_count": len(sessions),
                "target_date": goal["target_date"],
            }
        )
    return pd.DataFrame(records, columns=columns)


def _period_totals(df: pd.DataFrame, freq: str, label: str) -> pd.DataFrame:
    """Group total session minutes into day/week/month buckets."""
    if df.empty:
        return pd.DataFrame(columns=[label, "minutes", "sessions"])
    grouped = (
        df.groupby(df["date"].dt.to_period(freq))
        .agg(minutes=("minutes", "sum"), sessions=("minutes", "size"))
        .reset_index()
    )
    grouped = grouped.rename(columns={"date": label}).sort_values(label)
    grouped[label] = grouped[label].astype(str)
    return grouped.reset_index(drop=True)


def daily_totals(df: pd.DataFrame) -> pd.DataFrame:
    return _period_totals(df, "D", "day")


def weekly_totals(df: pd.DataFrame) -> pd.DataFrame:
    return _period_totals(df, "W-MON", "week")


def monthly_totals(df: pd.DataFrame) -> pd.DataFrame:
    return _period_totals(df, "M", "month")


def current_streak(df: pd.DataFrame, today: Optional[date] = None) -> int:
    """Count consecutive days with a session, ending today or yesterday.

    Ending "yesterday" is allowed so the streak does not appear broken before
    the user has had a chance to log today's work.
    """
    if df.empty:
        return 0
    today = today or date.today()
    active = {d.date() for d in df["date"].dropna()}
    if not active:
        return 0

    # Anchor on today if present, otherwise yesterday; otherwise streak is 0.
    anchor = today if today in active else today - timedelta(days=1)
    if anchor not in active:
        return 0

    streak = 0
    cursor = anchor
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def planner_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Headline planner numbers for the summary cards."""
    if df.empty:
        return {
            "total_minutes": 0,
            "total_hours": 0.0,
            "session_count": 0,
            "active_days": 0,
            "streak": 0,
            "avg_minutes_per_session": 0.0,
        }
    total_minutes = int(df["minutes"].sum())
    return {
        "total_minutes": total_minutes,
        "total_hours": round(total_minutes / 60, 1),
        "session_count": int(len(df)),
        "active_days": int(df["date"].dt.date.nunique()),
        "streak": current_streak(df),
        "avg_minutes_per_session": round(total_minutes / max(len(df), 1), 1),
    }


def month_calendar(df: pd.DataFrame, year: int, month: int) -> list[list[dict[str, Any]]]:
    """Build a calendar grid (list of weeks) for the goal tracker.

    Each cell is ``{"day": int|None, "date": str|None, "minutes": int,
    "has_session": bool}``. Weekday order is Monday..Sunday.
    """
    minutes_by_day: dict[str, int] = {}
    if not df.empty:
        in_month = df[(df["date"].dt.year == year) & (df["date"].dt.month == month)]
        minutes_by_day = (
            in_month.assign(day=in_month["date"].dt.date.astype(str))
            .groupby("day")["minutes"]
            .sum()
            .to_dict()
        )

    cal = calendar.Calendar(firstweekday=0)  # Monday first
    weeks: list[list[dict[str, Any]]] = []
    for week in cal.monthdatescalendar(year, month):
        row: list[dict[str, Any]] = []
        for day in week:
            if day.month != month:
                row.append({"day": None, "date": None, "minutes": 0, "has_session": False})
                continue
            iso = day.isoformat()
            mins = int(minutes_by_day.get(iso, 0))
            row.append(
                {
                    "day": day.day,
                    "date": iso,
                    "minutes": mins,
                    "has_session": mins > 0,
                }
            )
        weeks.append(row)
    return weeks
