# IG Snapshot

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![SQL](https://img.shields.io/badge/SQL-SQLite-003B57?logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-not%20specified-lightgrey)

IG Snapshot is a Windows-based Instagram competitor tracker built on the official Instagram Graph API
(*Business Discovery*). Every night it collects public metrics for a configured list of Business/Creator
accounts, stores the measurements locally in SQLite, rebuilds monthly / daily / per-account Excel reports,
sends a Telegram summary, and answers ad-hoc questions through a Telegram bot ("what happened yesterday",
"this week's reels", ...).

It only **reads** data that the Graph API exposes for the granted token. It does not publish, like, follow, or
automate any activity on Instagram. No external database or hosting is required; everything runs on one PC.

For digital media teams, nightly collection replaces repetitive manual checks with a consistent historical
record. Follower growth, post engagement, and publishing activity can be compared across accounts and periods,
helping analysts spot changes and plan content with evidence. This is an automated extraction and reporting
pipeline, not a source of metrics that Meta does not expose.

Turkish documentation: [`docs/NASIL_CALISIYOR.md`](docs/NASIL_CALISIYOR.md) (how it works — diagrams and
pseudo-code, which data lives in which table), [`docs/SISTEM_DOKUMANI.md`](docs/SISTEM_DOKUMANI.md)
(full technical reference) and [`docs/CALISMA_GUNLUGU.md`](docs/CALISMA_GUNLUGU.md) (work log).

---

## What it tracks

For every monitored account, per day:

- Followers, following and total media count
- Each recent post's views, likes and comments (a new measurement row per post per day)
- Post metadata: type (Reels / Photo / Carousel / Video), publish time, caption, permalink

Derived from those measurements:

- **Reels vs Feed** split everywhere (Feed = photos + carousels + feed videos)
- **Gained** views / likes / comments per day, month, week — the growth of *all* tracked posts in the period
- **Cohort** view — what the posts *published on a given day* have accumulated (first day / month end / current)
- Cross-month history and cumulative follower growth, with charts
- Data-quality warnings (missing posts, hidden likes, abnormal follower jumps, failed accounts)

Not available from the API: stories, saves, shares, reach, views for photos/carousels, and which post caused a
follower change. Only Business and Creator accounts can be queried.

---

## How it works

```
Windows Task Scheduler
 ├─ "IG Snapshot" (daily 23:30, catches up if the PC was off)
 │    snapshot → SQLite → monthly + daily-cohort + overall + per-account reports
 │             → Telegram evening summary (2 messages)
 │             → Telegram weekly report (Sunday nights) and month-end summary (first days of a month)
 └─ "IG Bot" (starts at logon, runs continuously, restarts on failure)
      Telegram long polling → natural-language questions → answers built from the same database
```

### Post tracking lifecycle

1. A post is fetched nightly while it is younger than `TRACK_DAYS` (default 45). Pagination goes back only as
   far as needed (posts are ordered newest first).
2. When a post's daily gain drops below `STOP_RATIO` × the previous day's gain (after at least `MIN_TRACK_DAYS`
   days), tracking stops and the post keeps its last value. This is an internal mechanism — reports never label
   posts as "completed".
3. Nothing is deleted: every daily measurement stays in the database so that day-by-day history is available
   for year-over-year analysis. (Optional pruning of completed posts' intermediate rows exists behind
   `PRUNE_AFTER_DAYS`, off by default.)

### Rate limits

The Graph API applies per-app limits over a rolling hour (`X-App-Usage`). The client reads those headers after
every call; when usage exceeds `USAGE_PAUSE_PCT` the run pauses (`USAGE_SLEEP_SEC`) and continues. Rate-limit
errors are retried with exponential backoff; if they persist the remaining accounts are skipped until the next
night. `python -m ig_snapshot limit-test` measures the actual per-call cost with your accounts.

---

## Requirements

- Windows 10/11, Python 3.11+ (developed on 3.14)
- A Meta app with the Instagram Graph API and a long-lived token (User or Page token) that can access an
  Instagram Business/Creator account linked to a Facebook Page
- Optional: a Telegram bot (via @BotFather) for notifications and the chat bot

## Tech stack

| Technology | Role |
|---|---|
| Python | Collection, processing, reports, and command-line interface |
| SQLite / SQL | Local historical storage and queries |
| `requests` | Meta and Telegram API requests |
| `python-dotenv` | Local `.env` configuration |
| `openpyxl` | Excel report generation |
| Python `logging` | Run diagnostics and API usage records |

Pandas is not currently a project dependency; see `requirements.txt` for the installed packages.

## Installation

```powershell
git clone https://github.com/Mgucsav/IG-snapshot.git
cd IG-snapshot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

1. Put the Instagram token into `.env` (`IG_ACCESS_TOKEN=`). Leave `IG_USER_ID` empty; `check` discovers it.
2. List the accounts to monitor in `accounts.txt`, grouped:

   ```
   [biz]          # your own accounts
   your_account
   [rakipler]     # competitors
   competitor_one
   @competitor_two
   https://www.instagram.com/competitor_three/
   ```
3. Validate:

   ```powershell
   .\.venv\Scripts\python.exe -m ig_snapshot check
   ```

   Shows token validity and expiry, writes `IG_USER_ID`, and runs a trial query on the first account.

4. Run the collection and inspect the result:

   ```powershell
   .\.venv\Scripts\python.exe -m ig_snapshot snapshot
   .\.venv\Scripts\python.exe -m ig_snapshot status
   ```

### Telegram (optional)

1. Create a bot with @BotFather and put its token into `.env` (`TELEGRAM_BOT_TOKEN=`).
2. Open the bot in Telegram and press *Start* (or add it to a group and post a message).
3. `python -m ig_snapshot telegram-test` — discovers the chat id, stores it, sends a test message.

### Scheduling

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1          # nightly snapshot at 23:30
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 22:00
powershell -ExecutionPolicy Bypass -File scripts\register_bot.ps1           # Telegram bot (at logon)
powershell -ExecutionPolicy Bypass -File scripts\register_bot.ps1 -Restart  # after updating the code
```

The PC must be on and the user logged in (a locked screen is fine). A missed nightly run executes when the PC
comes back.

---

## Commands

All commands: `.venv\Scripts\python -m ig_snapshot <command>`

| Command | Purpose |
|---|---|
| `check` | Validate the token, discover `IG_USER_ID`, trial query |
| `snapshot [--date YYYY-MM-DD] [--no-report]` | The full nightly run |
| `report [--month YYYY-MM] [--all] [--overall]` | Rebuild reports from the database |
| `status` | Last run, per-account last measurement, errors |
| `export [--month YYYY-MM] [--all] [--db]` | Raw data dump: CSV + Excel (+ a copy of the database) |
| `ledger [--date YYYY-MM-DD]` | Write the daily / weekly / monthly CSV ledger |
| `rebuild [--compare]` | Rebuild the database from the CSV ledger (backup drill) |
| `ask <text>` | Try a bot question without Telegram, e.g. `ask dün feed` |
| `bot` | Run the Telegram bot in the foreground |
| `week-summary [--end YYYY-MM-DD] [--send]` | Weekly report (print, or send to Telegram) |
| `month-summary [--month YYYY-MM] [--send]` | Month-end summary (print, or send) |
| `telegram-test` | Verify the bot, discover the chat id, send a test message |
| `limit-test [accounts] [--calls N]` | Measure per-call cost and hourly capacity |
| `token-refresh` | Exchange a still-valid long-lived token for a new one (`FB_APP_ID`/`FB_APP_SECRET`) |

### Telegram bot questions (Turkish)

| Question | Answer |
|---|---|
| `dün ne oldu`, `bugün`, `bu hafta`, `geçen ay`, `eylül`, `son 3 gün`, `2026-08` | Per-account blocks (followers, posts, gained views/likes/comments) grouped as "Biz / Rakipler", then Reels top 5 / worst 5 and Feed top 5 / worst 5 |
| `dün feed`, `bu hafta reels`, `geçen hafta feed @account` | Posts of that period per account, then overall top 5 / worst 5 |
| `durum` | Last run, accounts, token days left |

---

## Outputs

```
reports/
  2026-09/
    rapor-2026-09.md / .xlsx   monthly report: summary, Reels-Feed, types, daily, all posts
    gunluk-2026-09.xlsx        daily cohort: per day and account, pivots with charts, posts
                               (first day / month end / current / last-24h gain), highlights
    icerikler-2026-09.csv      all posts published in the month (;-separated, UTF-8 BOM)
  genel/
    genel-rapor.md / .xlsx     cross-month history, follower growth chart, monthly views/likes charts
  kanallar/
    <account>.xlsx             per-account daily log, post×day view and like matrices, post list
  disa-aktarim/2026-09/
    profil-olcumleri.csv       raw: followers per account per day
    gonderiler.csv             raw: posts published in the period with their final values
    gonderi-olcumleri.csv      raw: every daily measurement (post × day)
    veri-2026-09.xlsx          the same three tables in one workbook
    ig_snapshot-YYYYMMDD.db    a frozen copy of the database (monthly archive)
```

When a month ends, the first nightly run of the new month exports the finished month automatically
(raw CSV/Excel plus a database copy) — once, tracked in the `meta` table.

### CSV ledger (`data/`)

Besides the database, every nightly run writes a plain-CSV ledger — the human-readable, immutable record
that also serves as the backup:

```
data/
  gonderiler.csv            post catalog: type, publish time, caption, link (source for a rebuild)
  gunluk/2026-11-07.csv     that day only: each post's value and that day's gain, "ömrü doldu" stamp
  gunluk/ozet/2026-11-07.csv   that day's per-account summary
  haftalik/2026-W45.csv     Mon→Sun cumulative, rewritten nightly, new file every Monday
  haftalik/gonderi/2026-W45.csv
  aylik/2026-11.csv         month-to-date cumulative, new file on the 1st
  aylik/gonderi/2026-11.csv
```

Daily files are written once and never touched again. Weekly and monthly files keep updating while their
posts are still tracked, then settle. `python -m ig_snapshot rebuild --compare` restores the whole database
from these files and verifies it row for row — the backup is tested, not assumed.

Telegram:

- **Evening summary** — message 1: one block per account (👥 followers, 📝 posts, ▶️ views gained with
  "today's content / archive" split, ❤️ likes Reels/Feed, 💬 comments), failed accounts, data checks, token days;
  message 2: today's Reels top 5 / worst 5 and Feed top 5 / worst 5.
- **Weekly report** — Sunday after the nightly run (Monday→Sunday), same two-message layout with a comparison
  to the previous week. First week controlled by `WEEKLY_FROM`.
- **Month-end summary** — once, in the first days of a new month: account summaries and the month's top posts.
- Alerts: token expiring in ≤ `TOKEN_WARN_DAYS` (daily), token invalid, run crashed.

All report files are regenerated from the database every night (the current and the previous month). Editing
them by hand is not preserved; a file open in Excel is skipped that night and logged.

---

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `IG_ACCESS_TOKEN` | — | Graph API token (required) |
| `IG_USER_ID` | auto | Your Instagram professional account id |
| `FB_APP_ID`, `FB_APP_SECRET` | — | Only for `token-refresh` |
| `GRAPH_API_VERSION` | `v25.0` | |
| `MEDIA_PAGE_SIZE` | `50` | Posts per API call |
| `MEDIA_MAX` | `600` | Hard cap of posts per account per run |
| `TRACK_DAYS` | `45` | Posts are fetched while younger than this |
| `STOP_RATIO` | `0.2` | Tracking stops when daily gain < ratio × previous day's gain |
| `MIN_TRACK_DAYS` | `5` | Minimum age before a post can stop being tracked |
| `PRUNE_AFTER_DAYS` | `0` | 0 = keep every measurement (default); >0 enables pruning of stopped posts' intermediate rows |
| `REQUEST_PAUSE` | `1.5` | Seconds between accounts |
| `USAGE_PAUSE_PCT` | `70` | Pause when `X-App-Usage` exceeds this percentage |
| `USAGE_SLEEP_SEC` | `600` | Pause length |
| `CHANNEL_LOG_DAYS` | `90` | Width of the post×day matrices |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | Notifications and bot (silent if empty) |
| `TOKEN_WARN_DAYS` | `5` | Daily warning when the token expires within this many days |
| `WEEKLY_FROM` | — | First Monday for which the weekly report is sent |

---

## Data model (SQLite, `data/ig_snapshot.db`)

| Table | Row | Purpose |
|---|---|---|
| `accounts` | account | id, name, last success / error |
| `profile_snapshots` | account × day | followers, following, media count |
| `media` | post | type, publish time (local), caption, permalink, `completed_at` |
| `media_snapshots` | post × day | views, likes, comments |
| `runs` | run | timing and result |
| `meta` | key | e.g. last weekly / month-end message sent |

Account renames are detected by the Instagram account id and merged automatically; the new handle still has to be
written into `accounts.txt` because the API is queried by username.

---

## Public repository and private runtime data

Credentials are read from the local `.env` file; the source does not require hardcoded credentials. This
repository contains source code, scripts, documentation, `requirements.txt`, and the `.env.example` template.
The following are ignored by `.gitignore` and must never be committed:

| Item | Reason |
|---|---|
| `.env` | Instagram token, Telegram token, chat id |
| `accounts.txt` | Identifies the monitored accounts |
| `data/` | SQLite history, control exports, bot lock |
| `reports/` | Captions, links, metrics, generated workbooks |
| `logs/` | Operational output; keep it private even when sensitive fields are masked |
| `.venv/`, `__pycache__/`, `.obsidian/` | Environment and editor state |

If a credential is ever exposed, revoke or rotate it immediately (Meta: generate a new token; Telegram: `/revoke`
in @BotFather) and update `.env`.

## Project structure

```text
IG-snapshot/
├── .venv/              # Local Python environment (ignored)
├── data/               # SQLite database and CSV ledger (ignored)
├── docs/               # Turkish system and operating documentation
├── ig_snapshot/        # Graph API client, collection, storage, reporting, CLI
├── logs/               # Runtime logs (ignored)
├── reports/            # Generated Excel, CSV, and Markdown reports (ignored)
├── scripts/            # Windows task and command wrappers
├── tests/              # Offline regression tests
├── .env.example        # Configuration template; no credential values
├── requirements.txt    # Python dependencies
└── README.md
```

## License

No license file is currently included. Add one before describing the repository as open source or granting
reuse rights.

## Responsible use

Use this tool only with a token you are authorized to use and within Meta's Platform Terms. The data collected
is account data exposed by the Graph API for analytical purposes; handle it under applicable privacy
requirements, respect rate limits, and keep the local database and reports private.
