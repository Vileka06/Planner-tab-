# 📊 GitHub Contribution Planner

An original, beginner-friendly Python dashboard that (1) explores a user's
**public** GitHub activity and (2) provides a persistent **daily coding
planner** stored in SQLite.

Built with **Python · Streamlit · GitHub REST API · Pandas · Plotly · SQLite**.

> **Honesty first.** This project only reads public data GitHub already
> publishes. It never generates fake contributions, alters commit timestamps,
> or misrepresents coding activity. Tokens are never hardcoded.

---

## ✨ Features

1. Modern dashboard with summary cards and interactive charts.
2. Enter a GitHub username to view **public** profile and repository info.
3. Analyze real repository/commit activity with limitations clearly explained.
4. Daily planner: create goals, record sessions, mark tasks complete.
5. Goals and sessions persist in SQLite across restarts.
6. Weekly & monthly progress charts plus a calendar-style goal tracker.
7. Loading spinners, input validation, error handling and helpful messages.
8. Clean separation: API logic and database logic live in their own modules.
9. No fake activity, no timestamp tampering.
10. No secrets in code — tokens come from env vars or Streamlit secrets.

---

## 🏗️ Architecture

```
        app.py  (Streamlit UI — the "face")
          │            │              │
          ▼            ▼              ▼
 github_client.py  activity_analyzer.py  goal_tracker.py
   (REST API)        (Pandas math)       (planner logic)
          │                                 │
       GitHub API                      database.py
                                        (SQLite — "vault")
```

- **`database.py`** — the *only* file that runs SQL. Creates `goals` and
  `sessions` tables and offers safe CRUD functions returning plain dicts.
- **`github_client.py`** — the *only* file that makes HTTP calls. Handles
  errors, rate limits, timeouts and optional auth. Never imports Streamlit.
- **`activity_analyzer.py`** — pure Pandas functions, no I/O; easy to test.
- **`goal_tracker.py`** — validates planner input and computes progress,
  streaks and calendar grids by calling `database.py`.
- **`app.py`** — Streamlit UI that wires everything together.

---

## 📁 Project structure

```
green/
├── app.py                 # Streamlit UI
├── github_client.py       # GitHub REST API wrapper
├── activity_analyzer.py   # Pandas analytics
├── goal_tracker.py        # Planner business logic
├── database.py            # SQLite persistence
├── requirements.txt       # Dependencies
└── README.md              # This file
```

`planner.db` is created automatically on first run.

---

## 🍎 Setup on macOS (VS Code)

### 1. Open the project
Open the `green` folder in VS Code (`File ▸ Open Folder…`).

### 2. Create and activate a virtual environment
In the VS Code terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Then select the interpreter: `Cmd+Shift+P ▸ Python: Select Interpreter ▸ ./.venv/bin/python`.

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## ▶️ Running the app

```bash
streamlit run app.py
```

Streamlit opens `http://localhost:8501` in your browser. Stop it with `Ctrl+C`.

---

## 🔑 Optional: raise the GitHub API rate limit

Anonymous access allows only **60 requests/hour**. A personal access token
raises this to **5000/hour**. The app checks for a token in this order:

1. **Streamlit secrets** — create `.streamlit/secrets.toml`:

   ```toml
   GITHUB_TOKEN = "ghp_your_token_here"
   ```

2. **Environment variable**:

   ```bash
   export GITHUB_TOKEN=ghp_your_token_here
   ```

The token is read at runtime and passed to `GitHubClient`; it is **never**
written into the code or shown in the UI. Add `.streamlit/` to `.gitignore`.

---

## 🧪 How to test

### A. Quick smoke test (no browser)
Verify the modules import and the database works:

```bash
python -c "import database, goal_tracker; database.init_db(); print('db ok')"
python -c "import activity_analyzer as a; print(a.summarize_repos(a.repos_to_dataframe([])))"
python -c "import github_client as g; print(g.is_valid_username('torvalds'), g.is_valid_username('bad name'))"
```

Expected output:

```
db ok
{'repo_count': 0, ...}
True False
```

### B. Manual UI checklist
1. Launch `streamlit run app.py`.
2. **GitHub tab:** enter `torvalds` → click **Analyze**. You should see the
   profile, summary cards, language pie, top-repos bar, and commit charts.
3. Try an invalid name (e.g. `bad name!`) → you should get a validation error.
4. Try a non-existent user (e.g. `this-user-should-not-exist-xyz123`) → a
   friendly "not found" message.
5. **Planner tab:** create a goal, log a session against it, watch the cards,
   weekly/monthly charts and calendar update.
6. Mark the goal **Complete**, then **Reopen**.
7. Restart Streamlit and confirm your goal/session data is still there
   (proof that SQLite persistence works).

> A feature is only considered *verified* here once it has actually been run.
> Use the checklist above and note any failures.

---

## 🎤 Explaining it in an interview

- **Separation of concerns:** UI (`app.py`), network (`github_client.py`),
  analytics (`activity_analyzer.py`), business rules (`goal_tracker.py`) and
  storage (`database.py`) are independent, so each is testable in isolation.
- **Data honesty:** the app reports only what the API returns and labels its
  data as *sampled*, with an explicit limitations list.
- **Validation & errors:** the client translates HTTP status codes into
  friendly exceptions; the tracker validates dates/minutes before storage.
- **Security:** tokens come from the environment/secrets, never the codebase.
- **Persistence:** SQLite with parameterized queries and safe transactions via
  a connection context manager.

### Key functions to know

- `GitHubClient._get()` — central request handler that maps status codes
  (404, 403/429, 401) to custom exceptions.
- `activity_analyzer.repos_to_dataframe()` / `commits_to_dataframe()` — clean,
  typed DataFrames from raw JSON.
- `activity_analyzer.summarize_commits()` — headline commit stats.
- `goal_tracker.current_streak()` — consecutive-day streak logic.
- `goal_tracker.month_calendar()` — builds the weekly grid for the tracker.
- `database._connect()` — context manager that commits/rolls back/closes safely.

---

## ⚠️ Known limitations

- Only public repositories are accessible.
- Commit data covers the default branch, limited to the pages fetched.
- Anonymous API access is capped at 60 requests/hour.
- Commit attribution depends on GitHub's recorded dates, which repository
  owners control; the app does not verify or modify them.

---

## 📜 License

MIT — free to learn from and extend.
