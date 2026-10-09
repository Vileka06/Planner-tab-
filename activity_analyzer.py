"""Turn raw GitHub API dicts into DataFrames and summary numbers with Pandas.

Everything here is a pure function: given the same input it returns the same
output, performs no I/O, and never mutates its arguments. That makes this
module the easiest part of the project to unit test.

IMPORTANT (honesty): these functions only summarize the commits and repos that
were actually fetched from the API. They never fabricate activity, smooth over
gaps, or adjust timestamps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

# Shown in the UI so the user understands the boundaries of the data.
DATA_LIMITATIONS: tuple[str, ...] = (
    "Only public data is shown; private repositories are never included.",
    "Commit data comes from the repository's default branch and the pages we "
    "fetched — very old or deeply buried commits may not appear.",
    "Commits are attributed using the date GitHub records, which the repository "
    "owner controls; we do not verify or alter it.",
    "Anonymous API access is limited to 60 requests/hour; adding a token raises "
    "this to 5000 requests/hour.",
)


# --------------------------------------------------------------------------- #
# Repository analytics
# --------------------------------------------------------------------------- #
def repos_to_dataframe(repos: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a tidy DataFrame of repositories with parsed dates."""
    columns = [
        "name", "full_name", "language", "stargazers_count", "forks_count",
        "open_issues_count", "fork", "archived", "created_at", "pushed_at",
        "updated_at", "size",
    ]
    if not repos:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(repos)
    for col in ("created_at", "pushed_at", "updated_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    for col in ("stargazers_count", "forks_count", "open_issues_count", "size"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    # Normalize missing language labels so grouping is consistent.
    df["language"] = df.get("language", pd.Series(dtype="object")).fillna("Unknown")
    return df


def summarize_repos(df: pd.DataFrame) -> dict[str, Any]:
    """Compute headline repo numbers for the summary cards."""
    if df.empty:
        return {
            "repo_count": 0,
            "original_count": 0,
            "forked_count": 0,
            "total_stars": 0,
            "total_forks": 0,
            "total_open_issues": 0,
            "top_language": "N/A",
        }
    original = df[~df["fork"].astype(bool)] if "fork" in df else df
    top_language = (
        df.loc[df["language"] != "Unknown", "language"].mode()
        if not df.loc[df["language"] != "Unknown"].empty
        else pd.Series(dtype="object")
    )
    return {
        "repo_count": int(len(df)),
        "original_count": int(len(original)),
        "forked_count": int(df["fork"].astype(bool).sum()) if "fork" in df else 0,
        "total_stars": int(df["stargazers_count"].sum()),
        "total_forks": int(df["forks_count"].sum()),
        "total_open_issues": int(df["open_issues_count"].sum()),
        "top_language": top_language.iloc[0] if not top_language.empty else "N/A",
    }


def language_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Return repo counts per language, sorted and with a percentage column."""
    if df.empty:
        return pd.DataFrame(columns=["language", "repos", "percent"])
    counts = (
        df["language"]
        .value_counts()
        .rename_axis("language")
        .reset_index(name="repos")
    )
    total = counts["repos"].sum() or 1
    counts["percent"] = (counts["repos"] / total * 100).round(1)
    return counts


# --------------------------------------------------------------------------- #
# Commit analytics
# --------------------------------------------------------------------------- #
def commits_to_dataframe(commits: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a DataFrame of commits with a parsed UTC datetime column."""
    columns = [
        "sha", "message", "author_name", "author_login", "authored_at",
        "committed_at", "html_url",
    ]
    if not commits:
        return pd.DataFrame(columns=columns + ["date"])

    df = pd.DataFrame(commits)
    # Prefer the authored date, fall back to the committed date.
    raw_date = df.get("authored_at")
    if raw_date is None:
        raw_date = df.get("committed_at")
    df["date"] = pd.to_datetime(raw_date, errors="coerce", utc=True)
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def _group_commits_by_period(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Shared helper: bucket commits into daily/weekly/monthly rows."""
    if df.empty:
        return pd.DataFrame(columns=["period", "commits"])
    # tz-naive calendar periods make charts and labels cleaner.
    periods = df["date"].dt.tz_convert(None).dt.to_period(freq)
    grouped = (
        periods.value_counts()
        .rename_axis("period")
        .reset_index(name="commits")
        .sort_values("period")
    )
    grouped["period"] = grouped["period"].astype(str)
    return grouped.reset_index(drop=True)


def commit_activity_by_day(df: pd.DataFrame) -> pd.DataFrame:
    """Commits per calendar day (YYYY-MM-DD)."""
    return _group_commits_by_period(df, "D")


def commit_activity_by_week(df: pd.DataFrame) -> pd.DataFrame:
    """Commits per ISO week (YYYY-MM-DD marking the week start)."""
    return _group_commits_by_period(df, "W-MON")


def commit_activity_by_month(df: pd.DataFrame) -> pd.DataFrame:
    """Commits per calendar month (YYYY-MM)."""
    return _group_commits_by_period(df, "M")


def summarize_commits(df: pd.DataFrame) -> dict[str, Any]:
    """Compute headline commit numbers for the summary cards."""
    if df.empty:
        return {
            "commit_count": 0,
            "active_days": 0,
            "first_commit": None,
            "last_commit": None,
            "avg_per_active_day": 0.0,
            "busiest_day": None,
            "busiest_day_commits": 0,
        }
    daily = commit_activity_by_day(df)
    busiest = daily.sort_values("commits", ascending=False).iloc[0]
    return {
        "commit_count": int(len(df)),
        "active_days": int(daily["period"].nunique()),
        "first_commit": df["date"].min().strftime("%Y-%m-%d"),
        "last_commit": df["date"].max().strftime("%Y-%m-%d"),
        "avg_per_active_day": round(len(df) / max(len(daily), 1), 2),
        "busiest_day": busiest["period"],
        "busiest_day_commits": int(busiest["commits"]),
    }


# --------------------------------------------------------------------------- #
# Fetch metadata
# --------------------------------------------------------------------------- #
def build_fetch_note(
    repos_fetched: int,
    commits_fetched: int,
    used_token: bool,
    fetched_at: Optional[datetime] = None,
) -> str:
    """A human-readable provenance line so the UI never overstates the data."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    auth = "authenticated" if used_token else "anonymous (60 req/hour)"
    return (
        f"Fetched {repos_fetched} repositories and {commits_fetched} commits "
        f"from the public GitHub REST API at "
        f"{fetched_at.strftime('%Y-%m-%d %H:%M UTC')} — access: {auth}."
    )
