"""Streamlit dashboard for the GitHub Contribution Planner.

This file is the presentation layer only. It talks to the other modules:
  * github_client    -> fetches public GitHub data
  * activity_analyzer -> turns that data into DataFrames/summaries
  * goal_tracker     -> planner rules, progress and calendar math
  * database         -> persistence (used indirectly via goal_tracker)

It contains no SQL and makes no HTTP requests of its own. It also never stores
or prints access tokens: a token, if any, is read once from Streamlit secrets
or an environment variable and passed to the client.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import activity_analyzer as analyzer
import goal_tracker as tracker
from github_client import (
    GitHubClient,
    GitHubError,
    UserNotFoundError,
    RateLimitError,
)
import database as db

# --------------------------------------------------------------------------- #
# Page configuration and styling
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="GitHub Contribution Planner",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    .gcp-card {
        background: #ffffff;
        border: 1px solid #e6e8eb;
        border-radius: 14px;
        padding: 18px 20px;
        box-shadow: 0 2px 8px rgba(16, 24, 40, 0.05);
        height: 100%;
    }
    .gcp-card .label {
        font-size: 0.82rem; color: #667085; font-weight: 600;
        text-transform: uppercase; letter-spacing: .04em;
    }
    .gcp-card .value {
        font-size: 1.9rem; color: #101828; font-weight: 700; margin-top: 4px;
    }
    .gcp-card .sub { font-size: 0.8rem; color: #98a2b3; margin-top: 2px; }
    .gcp-hero {
        background: linear-gradient(120deg, #1f2937 0%, #3b4a63 100%);
        color: #fff; border-radius: 16px; padding: 22px 26px; margin-bottom: 8px;
    }
    .gcp-hero h1 { margin: 0; font-size: 1.6rem; }
    .gcp-hero p { margin: 6px 0 0; opacity: .85; font-size: .95rem; }
    .cal-header { text-align:center; font-weight:700; color:#475467; font-size:.8rem; }
    .cal-cell {
        border-radius: 8px; padding: 8px 4px; margin: 2px 0; text-align: center;
        border: 1px solid #eaecf0; min-height: 52px;
    }
    .cal-empty { background: transparent; border: none; }
    .cal-day { font-weight: 600; color: #344054; font-size: .9rem; }
    .cal-mins { font-size: .72rem; color: #475467; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def get_github_token() -> tuple[str | None, str]:
    """Read a token from Streamlit secrets first, then the environment.

    Never hardcoded. Returns (token, source_description).
    """
    try:
        # st.secrets raises if no secrets file is configured.
        token = st.secrets.get("GITHUB_TOKEN")  # type: ignore[attr-defined]
        if token:
            return str(token), "Streamlit secrets"
    except Exception:
        pass
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token, "environment variable"
    return None, "none (anonymous)"


@st.cache_resource
def get_db_initialized() -> bool:
    """Create tables once per session; cache_resource avoids repeats."""
    db.init_db()
    return True


def card(label: str, value, sub: str = "") -> str:
    """Return the HTML for one modern summary card."""
    return (
        f"<div class='gcp-card'><div class='label'>{label}</div>"
        f"<div class='value'>{value}</div>"
        f"<div class='sub'>{sub}</div></div>"
    )


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        f"<div class='gcp-hero'><h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# GitHub activity tab
# --------------------------------------------------------------------------- #
def fetch_github_bundle(username: str, scan_limit: int) -> dict:
    """Fetch profile, repos and commits. Data only — no placeholders."""
    token, source = get_github_token()
    client = GitHubClient(token=token)

    profile = client.get_user(username)
    repos = client.get_repos(username, max_repos=100)

    # Scan the most recently pushed non-fork repositories for commit activity.
    candidates = [r for r in repos if not r.get("fork")]
    candidates.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    selected = candidates[:scan_limit]

    all_commits: list[dict] = []
    per_repo_counts: list[dict] = []
    for repo in selected:
        try:
            commits = client.get_commits(username, repo["name"], max_pages=1)
        except RateLimitError:
            raise
        except GitHubError:
            commits = []  # e.g. empty repository; skip quietly
        all_commits.extend(commits)
        per_repo_counts.append(
            {"repo": repo["name"], "commits": len(commits),
             "pushed_at": repo.get("pushed_at")}
        )

    note = analyzer.build_fetch_note(
        repos_fetched=len(repos),
        commits_fetched=len(all_commits),
        used_token=token is not None,
    )
    return {
        "profile": profile,
        "repos": repos,
        "commits": all_commits,
        "per_repo_counts": per_repo_counts,
        "note": note,
        "used_token": token is not None,
        "token_source": source,
        "scanned_repos": len(selected),
    }


def render_github_tab() -> None:
    hero(
        "GitHub Activity",
        "Explore public profile and repository activity — only real data from the GitHub REST API.",
    )

    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        username = st.text_input(
            "GitHub username",
            placeholder="e.g. torvalds",
            help="Only public information can be retrieved.",
        )
    with c2:
        scan_limit = st.number_input(
            "Repos to scan", min_value=1, max_value=10, value=5,
            help="How many recently active repos to read commits from.",
        )
    with c3:
        st.write("")
        analyze = st.button("Analyze", use_container_width=True, type="primary")

    token, source = get_github_token()
    st.caption(
        f"API access: {'authenticated via ' + source if token else 'anonymous'} · "
        "Add a GITHUB_TOKEN secret to raise the rate limit."
    )

    if analyze:
        username = (username or "").strip()
        if not username:
            st.warning("Please enter a GitHub username first.")
        elif not username.replace("-", "").isalnum():
            st.error("Usernames can only contain letters, numbers and hyphens.")
        else:
            with st.spinner(f"Fetching public data for '{username}'…"):
                try:
                    st.session_state["gh"] = fetch_github_bundle(
                        username, int(scan_limit)
                    )
                    st.session_state["gh_user"] = username
                except UserNotFoundError:
                    st.error(f"User '{username}' was not found on GitHub.")
                except RateLimitError as exc:
                    st.error(f"{exc} {exc.reset_hint and '(resets at ' + exc.reset_hint + ')'}")
                except GitHubError as exc:
                    st.error(f"GitHub request failed: {exc}")
                except Exception as exc:  # last-resort safety net
                    st.error(f"Something unexpected happened: {exc}")

    bundle = st.session_state.get("gh")
    if not bundle:
        st.info("Enter a username above and click **Analyze** to load real data.")
        return

    profile = bundle["profile"]
    repos_df = analyzer.repos_to_dataframe(bundle["repos"])
    commits_df = analyzer.commits_to_dataframe(bundle["commits"])
    repo_summary = analyzer.summarize_repos(repos_df)
    commit_summary = analyzer.summarize_commits(commits_df)

    # ---- Profile row -------------------------------------------------- #
    pcol1, pcol2 = st.columns([1, 4])
    with pcol1:
        if profile.get("avatar_url"):
            st.image(profile["avatar_url"], width=140)
    with pcol2:
        st.subheader(profile.get("name") or profile.get("login"))
        st.markdown(
            f"[@{profile.get('login')}]({profile.get('html_url')})"
        )
        if profile.get("bio"):
            st.write(profile["bio"])
        meta_bits = []
        if profile.get("company"):
            meta_bits.append(f"🏢 {profile['company']}")
        if profile.get("location"):
            meta_bits.append(f"📍 {profile['location']}")
        if profile.get("blog"):
            meta_bits.append(f"🔗 {profile['blog']}")
        if meta_bits:
            st.caption(" · ".join(meta_bits))

    # ---- Summary cards ------------------------------------------------- #
    cards = st.columns(4)
    cards[0].markdown(
        card("Public repos", profile.get("public_repos", 0),
             f"{repo_summary['original_count']} original · {repo_summary['forked_count']} forks"),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        card("Followers", profile.get("followers", 0),
             f"following {profile.get('following', 0)}"),
        unsafe_allow_html=True,
    )
    cards[2].markdown(
        card("Stars earned", repo_summary["total_stars"],
             f"{repo_summary['total_forks']} forks"),
        unsafe_allow_html=True,
    )
    cards[3].markdown(
        card("Commits sampled", commit_summary["commit_count"],
             f"across {bundle['scanned_repos']} repos"),
        unsafe_allow_html=True,
    )

    st.caption(bundle["note"])

    # ---- Charts -------------------------------------------------------- #
    left, right = st.columns(2)

    with left:
        st.markdown("#### Language distribution")
        languages = analyzer.language_breakdown(repos_df)
        if languages.empty:
            st.info("No language information available.")
        else:
            fig = px.pie(
                languages, names="language", values="repos", hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=340)
            st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("#### Top repositories by stars")
        if repos_df.empty:
            st.info("No repositories found.")
        else:
            top = repos_df.sort_values("stargazers_count", ascending=False).head(8)
            fig = px.bar(
                top, x="stargazers_count", y="name", orientation="h",
                labels={"stargazers_count": "Stars", "name": ""},
                color="stargazers_count", color_continuous_scale="Blues",
            )
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10), height=340,
                yaxis=dict(autorange="reversed"), coloraxis_showscale=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Commit activity over time")
    if commits_df.empty:
        st.info(
            "No commit data was returned for the scanned repositories. This can "
            "happen with empty repos, or history beyond the first page we read."
        )
    else:
        weekday, monthly = st.columns(2)
        with weekday:
            weekly = analyzer.commit_activity_by_week(commits_df)
            fig = px.bar(
                weekly, x="period", y="commits",
                labels={"period": "Week starting", "commits": "Commits"},
                color_discrete_sequence=["#3b82f6"],
            )
            fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
        with monthly:
            m = analyzer.commit_activity_by_month(commits_df)
            fig = px.line(
                m, x="period", y="commits", markers=True,
                labels={"period": "Month", "commits": "Commits"},
                color_discrete_sequence=["#10b981"],
            )
            fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)

    # ---- Commit summary + table --------------------------------------- #
    if not commits_df.empty:
        s = st.columns(4)
        s[0].markdown(card("Sampled commits", commit_summary["commit_count"]), unsafe_allow_html=True)
        s[1].markdown(card("Active days", commit_summary["active_days"]), unsafe_allow_html=True)
        s[2].markdown(card("Avg / active day", commit_summary["avg_per_active_day"]), unsafe_allow_html=True)
        s[3].markdown(
            card("Busiest day", commit_summary["busiest_day"] or "—",
                 f"{commit_summary['busiest_day_commits']} commits"),
            unsafe_allow_html=True,
        )
        st.markdown("#### Recent commits")
        show = commits_df.sort_values("date", ascending=False).head(25)
        st.dataframe(
            show[["date", "message", "author_name", "sha"]],
            use_container_width=True, hide_index=True,
        )

    with st.expander("What this data does and does not include"):
        for item in analyzer.DATA_LIMITATIONS:
            st.markdown(f"- {item}")


# --------------------------------------------------------------------------- #
# Coding planner tab
# --------------------------------------------------------------------------- #
def render_goal_form() -> None:
    with st.form("goal_form", clear_on_submit=True):
        st.markdown("##### ➕ New goal")
        g1, g2 = st.columns([2, 1])
        with g1:
            title = st.text_input("Goal title", placeholder="Finish 3 LeetCode mediums")
        with g2:
            target_minutes = st.number_input(
                "Target minutes (0 = open-ended)", min_value=0, max_value=6000, value=180
            )
        description = st.text_area("Notes (optional)", height=68)
        target_date = st.date_input(
            "Target date (optional)", value=date.today() + timedelta(days=30)
        )
        submitted = st.form_submit_button("Create goal", type="primary")
        if submitted:
            try:
                tracker.create_goal(
                    title, description, target_date.isoformat(), int(target_minutes)
                )
                st.success(f"Goal '{title.strip()}' created.")
                st.rerun()
            except tracker.PlannerError as exc:
                st.error(str(exc))


def render_session_form(goals: list[dict]) -> None:
    with st.form("session_form", clear_on_submit=True):
        st.markdown("##### ⏱️ Log a coding session")
        s1, s2, s3 = st.columns([1, 1, 1])
        with s1:
            session_date = st.date_input("Date", value=date.today())
        with s2:
            minutes = st.number_input("Minutes", min_value=1, max_value=1440, value=60)
        with s3:
            options = {"(no specific goal)": None}
            options.update({g["title"]: g["id"] for g in goals})
            chosen = st.selectbox("Link to goal", list(options.keys()))
        notes = st.text_input("What did you do?", placeholder="e.g. solved 2 array problems")
        submitted = st.form_submit_button("Log session", type="primary")
        if submitted:
            try:
                tracker.log_session(
                    session_date.isoformat(), int(minutes),
                    goal_id=options[chosen], notes=notes,
                )
                st.success("Session recorded.")
                st.rerun()
            except tracker.PlannerError as exc:
                st.error(str(exc))


def render_calendar(df: pd.DataFrame) -> None:
    today = date.today()
    c1, c2 = st.columns(2)
    with c1:
        selected = st.date_input("Calendar month", value=today, key="cal_month")
    year, month = selected.year, selected.month

    st.markdown(
        f"<div class='cal-header'>{calendar_month_name(month)} {year}</div>",
        unsafe_allow_html=True,
    )
    headers = st.columns(7)
    for col, name in zip(headers, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
        col.markdown(f"<div class='cal-header'>{name}</div>", unsafe_allow_html=True)

    weeks = tracker.month_calendar(df, year, month)
    for week in weeks:
        cols = st.columns(7)
        for col, cell in zip(cols, week):
            if cell["day"] is None:
                col.markdown("<div class='cal-cell cal-empty'></div>", unsafe_allow_html=True)
                continue
            if cell["has_session"]:
                # Color intensity scales with minutes, capped for readability.
                shade = min(0.15 + cell["minutes"] / 300, 0.85)
                bg = f"rgba(16, 185, 129, {shade:.2f})"
                mins = f"{cell['minutes']} min"
            else:
                bg = "#f8fafc"
                mins = "—"
            col.markdown(
                f"<div class='cal-cell' style='background:{bg}'>"
                f"<div class='cal-day'>{cell['day']}</div>"
                f"<div class='cal-mins'>{mins}</div></div>",
                unsafe_allow_html=True,
            )


def calendar_month_name(month: int) -> str:
    names = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
    return names[month - 1]


def render_planner_tab() -> None:
    hero(
        "Daily Coding Planner",
        "Set goals, record real coding sessions, and watch your progress persist in SQLite.",
    )

    goals = tracker.list_goals()
    active_goals = [g for g in goals if g["status"] == "active"]

    # ---- Forms --------------------------------------------------------- #
    f1, f2 = st.columns(2)
    with f1:
        render_goal_form()
    with f2:
        render_session_form(goals)

    df = tracker.sessions_dataframe()
    summary = tracker.planner_summary(df)

    # ---- Summary cards ------------------------------------------------- #
    cards = st.columns(5)
    cards[0].markdown(card("Total hours", summary["total_hours"], "all recorded sessions"), unsafe_allow_html=True)
    cards[1].markdown(card("Sessions", summary["session_count"], "logged entries"), unsafe_allow_html=True)
    cards[2].markdown(card("Active days", summary["active_days"], "days with a session"), unsafe_allow_html=True)
    cards[3].markdown(card("Current streak", f"{summary['streak']} d", "consecutive days"), unsafe_allow_html=True)
    cards[4].markdown(card("Avg / session", f"{summary['avg_minutes_per_session']} m"), unsafe_allow_html=True)

    # ---- Goals list ---------------------------------------------------- #
    st.markdown("#### Goals")
    progress = tracker.progress_by_goal()
    if progress.empty:
        st.info("No goals yet — create one above to get started.")
    else:
        for row in progress.to_dict("records"):
            with st.container(border=True):
                gc1, gc2, gc3 = st.columns([4, 2, 2])
                with gc1:
                    done = "✅" if row["status"] == "completed" else "🎯"
                    st.markdown(f"**{done} {row['title']}**")
                    if row["target_date"]:
                        st.caption(f"Target: {row['target_date']} · {row['session_count']} sessions")
                    else:
                        st.caption(f"{row['session_count']} sessions")
                with gc2:
                    if row["percent"] is not None:
                        st.progress(
                            min(row["percent"] / 100, 1.0),
                            text=f"{row['logged_minutes']} / {row['target_minutes']} min "
                                 f"({row['percent']}%)",
                        )
                    else:
                        st.metric("Logged", f"{row['logged_minutes']} min")
                with gc3:
                    b1, b2 = st.columns(2)
                    if row["status"] == "active":
                        if b1.button("Complete", key=f"done_{row['id']}"):
                            tracker.mark_goal_complete(row["id"])
                            st.rerun()
                    else:
                        if b1.button("Reopen", key=f"reopen_{row['id']}"):
                            tracker.reopen_goal(row["id"])
                            st.rerun()
                    if b2.button("Delete", key=f"del_{row['id']}"):
                        tracker.remove_goal(row["id"])
                        st.rerun()

    # ---- Progress charts ---------------------------------------------- #
    st.markdown("#### Progress charts")
    if df.empty:
        st.info("Log a session to see your weekly and monthly progress.")
    else:
        wcol, mcol = st.columns(2)
        with wcol:
            weekly = tracker.weekly_totals(df)
            fig = px.bar(
                weekly, x="week", y="minutes",
                labels={"week": "Week starting", "minutes": "Minutes"},
                color_discrete_sequence=["#6366f1"],
            )
            fig.update_layout(
                title="Weekly minutes", height=320,
                margin=dict(t=40, b=10, l=10, r=10),
            )
            st.plotly_chart(fig, use_container_width=True)
        with mcol:
            monthly = tracker.monthly_totals(df)
            fig = px.line(
                monthly, x="month", y="minutes", markers=True,
                labels={"month": "Month", "minutes": "Minutes"},
                color_discrete_sequence=["#f59e0b"],
            )
            fig.update_layout(
                title="Monthly minutes", height=320,
                margin=dict(t=40, b=10, l=10, r=10),
            )
            st.plotly_chart(fig, use_container_width=True)

    # ---- Calendar ------------------------------------------------------ #
    st.markdown("#### Goal tracker calendar")
    render_calendar(df)

    # ---- Recent sessions ---------------------------------------------- #
    if not df.empty:
        with st.expander("Recent sessions"):
            recent = df.sort_values("date", ascending=False).head(20)
            st.dataframe(
                recent[["session_date", "minutes", "notes", "goal_id"]],
                use_container_width=True, hide_index=True,
            )
            st.caption("Delete a session by id:")
            del_id = st.number_input("Session id", min_value=1, step=1, value=1)
            if st.button("Delete session"):
                if tracker.remove_session(int(del_id)):
                    st.success(f"Session {int(del_id)} deleted.")
                    st.rerun()
                else:
                    st.warning("No session with that id was found.")


# --------------------------------------------------------------------------- #
# About tab
# --------------------------------------------------------------------------- #
def render_about_tab() -> None:
    hero("How it works", "A quick tour of the architecture and the honesty rules.")
    st.markdown(
        """
**Layers**

| File | Responsibility |
|------|----------------|
| `app.py` | Streamlit UI: cards, charts, forms. No SQL, no HTTP. |
| `github_client.py` | Talks to the public GitHub REST API only. |
| `activity_analyzer.py` | Pure Pandas analytics on the fetched data. |
| `goal_tracker.py` | Planner rules, progress, streaks, calendar math. |
| `database.py` | The only module that touches SQLite (`planner.db`). |

**Data integrity commitments**

- We only read public data that GitHub already publishes.
- We never generate fake contributions or change commit timestamps.
- Every commit figure is labelled as *sampled* from the pages we fetched.
- Tokens are read from Streamlit secrets or `GITHUB_TOKEN`, never hardcoded.

**Setup for authentication (optional)**

Create `.streamlit/secrets.toml` in the project folder:

```toml
GITHUB_TOKEN = "ghp_your_personal_access_token"
```

or export it: `export GITHUB_TOKEN=ghp_...`
"""
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    get_db_initialized()

    st.title("📊 GitHub Contribution Planner")
    st.caption("Understand your public GitHub activity and plan your daily coding — honestly.")

    tab_github, tab_planner, tab_about = st.tabs(
        ["🔎 GitHub Activity", "🗓️ Coding Planner", "ℹ️ How it works"]
    )
    with tab_github:
        render_github_tab()
    with tab_planner:
        render_planner_tab()
    with tab_about:
        render_about_tab()


if __name__ == "__main__":
    main()
