# IG Snapshot

An automated Instagram Business Discovery tracker that collects daily public metrics for configured Business and Creator accounts and generates monthly reports.

It tracks follower changes, Reels and Feed content, views, likes, comments, daily content cohorts, and account-level history. Stories are not included. Generated snapshots, reports, logs, the local database, and the account target list are intentionally kept out of Git because they may contain private or sensitive competitor-tracking data.

## Requirements

- Windows with PowerShell
- Python 3.10+
- An Instagram Graph API access token with Business Discovery access
- Business or Creator accounts to monitor

## Installation

```powershell
git clone <your-repository-url>
cd IG-snapshot
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally and set `IG_ACCESS_TOKEN`. Keep the real `.env` file private; it is ignored by Git. `IG_USER_ID` may be left empty and will be discovered by the `check` command.

Create a local `accounts.txt` file with one Instagram username, profile URL, or `@username` per line. This file is also ignored because it identifies the monitored accounts.

Validate the configuration:

```powershell
.venv\Scripts\python -m ig_snapshot check
```

## Daily snapshots

Run a snapshot manually:

```powershell
.venv\Scripts\python -m ig_snapshot snapshot
```

Register the Windows Task Scheduler job:

```powershell
.\scripts\register_task.ps1
.\scripts\register_task.ps1 -Time 22:00
.\scripts\register_task.ps1 -Remove
```

The default schedule runs daily at 23:30. The task calls `scripts\run_snapshot.bat`, writes local logs, and regenerates the current monthly and overall reports.

## Reports and commands

Reports are written locally under `reports/` as Markdown, Excel, and CSV files. The SQLite database is stored at `data/ig_snapshot.db`. None of these outputs are committed to Git.

```powershell
.venv\Scripts\python -m ig_snapshot report
.venv\Scripts\python -m ig_snapshot report --month 2026-08
.venv\Scripts\python -m ig_snapshot report --all
.venv\Scripts\python -m ig_snapshot report --overall
.venv\Scripts\python -m ig_snapshot status
```

Use the limit test to measure API capacity for selected accounts:

```powershell
.venv\Scripts\python -m ig_snapshot limit-test account1 account2 --calls 12
```

## Telegram notifications

Set `TELEGRAM_BOT_TOKEN` in the local `.env`, start a chat with the bot, and run:

```powershell
.venv\Scripts\python -m ig_snapshot telegram-test
```

Notifications include snapshot status, account errors, data-quality warnings, and token expiry warnings. Telegram configuration is optional.

## Configuration

See `.env.example` for all supported variables. Important settings include:

| Variable | Default | Description |
|---|---:|---|
| `IG_ACCESS_TOKEN` | required | Instagram Graph API access token |
| `IG_USER_ID` | automatic | Your Instagram professional account ID |
| `GRAPH_API_VERSION` | `v25.0` | Graph API version |
| `MEDIA_MAX` | `200` | Maximum media items fetched per account |
| `REQUEST_PAUSE` | `1.5` | Delay between account requests, in seconds |
| `USAGE_PAUSE_PCT` | `70` | Usage level that triggers an automatic pause |
| `USAGE_SLEEP_SEC` | `600` | Automatic pause duration, in seconds |
| `CHANNEL_LOG_DAYS` | `90` | Number of days retained in channel workbooks |
| `TELEGRAM_BOT_TOKEN` | optional | Telegram BotFather token |
| `TELEGRAM_CHAT_ID` | automatic | Chat ID discovered by `telegram-test` |
| `TOKEN_WARN_DAYS` | `5` | Days before expiry to start warnings |

## Repository privacy

The `.gitignore` rules exclude:

- `.env` and all local credentials
- `accounts.txt` and the monitored account list
- `data/`, including the SQLite database and raw control exports
- `reports/`, including CSV and Excel outputs
- `logs/`, virtual environments, and Python cache files

Only `.env.example` is intended to be committed. Never commit access tokens, bot tokens, account lists, API responses, or generated reports.

## Project structure

```text
ig_snapshot/
  api.py          Instagram Graph API client
  snapshot.py     Daily collection and storage
  report.py       Monthly Markdown, Excel, and CSV reports
  overall.py      Cross-month summary reports
  daily_report.py Daily content cohort reports
  channel_log.py  Per-account workbook generation
  notify.py       Telegram notifications
  db.py           SQLite schema and queries
  content.py      Content type classification
  limit_test.py   API capacity test
  cli.py           Command-line interface
scripts/          Task Scheduler and batch scripts
```