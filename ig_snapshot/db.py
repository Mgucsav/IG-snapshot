"""SQLite depolama: hesaplar, günlük profil ve gönderi snapshot'ları."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username    TEXT PRIMARY KEY,
    ig_id       TEXT,
    name        TEXT,
    first_seen  TEXT,
    last_ok     TEXT,
    last_error  TEXT
);
CREATE TABLE IF NOT EXISTS profile_snapshots (
    snapshot_date   TEXT NOT NULL,
    username        TEXT NOT NULL,
    followers_count INTEGER,
    follows_count   INTEGER,
    media_count     INTEGER,
    fetched_at      TEXT,
    PRIMARY KEY (snapshot_date, username)
);
CREATE TABLE IF NOT EXISTS media (
    media_id            TEXT PRIMARY KEY,
    username            TEXT NOT NULL,
    media_type          TEXT,
    media_product_type  TEXT,
    content_type        TEXT,
    caption             TEXT,
    permalink           TEXT,
    published_at        TEXT,   -- yerel saat, ISO
    published_month     TEXT,   -- YYYY-MM (yerel)
    first_seen          TEXT
);
CREATE TABLE IF NOT EXISTS media_snapshots (
    snapshot_date   TEXT NOT NULL,
    media_id        TEXT NOT NULL,
    like_count      INTEGER,
    comments_count  INTEGER,
    view_count      INTEGER,
    fetched_at      TEXT,
    PRIMARY KEY (snapshot_date, media_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    ok_count    INTEGER,
    fail_count  INTEGER,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS ix_media_user_month ON media(username, published_month);
CREATE INDEX IF NOT EXISTS ix_msnap_media_date ON media_snapshots(media_id, snapshot_date);
CREATE INDEX IF NOT EXISTS ix_psnap_user_date ON profile_snapshots(username, snapshot_date);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Sonradan eklenen sütunlar (mevcut veritabanlarını bozmadan)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(media)")}
    if "completed_at" not in cols:
        conn.execute("ALTER TABLE media ADD COLUMN completed_at TEXT")  # takip bitti: günlük artış söndü
        conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- yazma ------------------------------------------------------------------

def upsert_account(conn: sqlite3.Connection, username: str, ig_id: str | None, name: str | None) -> None:
    conn.execute("""
        INSERT INTO accounts (username, ig_id, name, first_seen, last_ok, last_error)
        VALUES (?, ?, ?, ?, ?, NULL)
        ON CONFLICT(username) DO UPDATE SET
            ig_id = COALESCE(excluded.ig_id, accounts.ig_id),
            name = COALESCE(excluded.name, accounts.name),
            last_ok = excluded.last_ok,
            last_error = NULL
    """, (username, ig_id, name, _now(), _now()))


def mark_account_error(conn: sqlite3.Connection, username: str, error: str) -> None:
    conn.execute("""
        INSERT INTO accounts (username, first_seen, last_error) VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET last_error = excluded.last_error
    """, (username, _now(), error[:500]))


def upsert_profile_snapshot(conn: sqlite3.Connection, snapshot_date: date, username: str,
                            followers: int | None, follows: int | None, media_count: int | None) -> None:
    conn.execute("""
        INSERT INTO profile_snapshots (snapshot_date, username, followers_count, follows_count, media_count, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, username) DO UPDATE SET
            followers_count = excluded.followers_count,
            follows_count = excluded.follows_count,
            media_count = excluded.media_count,
            fetched_at = excluded.fetched_at
    """, (snapshot_date.isoformat(), username, followers, follows, media_count, _now()))


def upsert_media(conn: sqlite3.Connection, media_id: str, username: str, media_type: str | None,
                 media_product_type: str | None, content_type: str, caption: str | None,
                 permalink: str | None, published_at: datetime | None) -> None:
    conn.execute("""
        INSERT INTO media (media_id, username, media_type, media_product_type, content_type,
                           caption, permalink, published_at, published_month, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(media_id) DO UPDATE SET
            media_type = excluded.media_type,
            media_product_type = excluded.media_product_type,
            content_type = excluded.content_type,
            caption = excluded.caption,
            permalink = excluded.permalink,
            published_at = COALESCE(excluded.published_at, media.published_at),
            published_month = COALESCE(excluded.published_month, media.published_month)
    """, (
        media_id, username, media_type, media_product_type, content_type,
        caption, permalink,
        published_at.isoformat(timespec="seconds") if published_at else None,
        published_at.strftime("%Y-%m") if published_at else None,
        _now(),
    ))


def upsert_media_snapshot(conn: sqlite3.Connection, snapshot_date: date, media_id: str,
                          likes: int | None, comments: int | None, views: int | None) -> None:
    conn.execute("""
        INSERT INTO media_snapshots (snapshot_date, media_id, like_count, comments_count, view_count, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, media_id) DO UPDATE SET
            like_count = excluded.like_count,
            comments_count = excluded.comments_count,
            view_count = excluded.view_count,
            fetched_at = excluded.fetched_at
    """, (snapshot_date.isoformat(), media_id, likes, comments, views, _now()))


def record_run(conn: sqlite3.Connection, started_at: str, ok: int, fail: int, notes: str = "") -> None:
    conn.execute("INSERT INTO runs (started_at, finished_at, ok_count, fail_count, notes) VALUES (?, ?, ?, ?, ?)",
                 (started_at, _now(), ok, fail, notes))


# --- okuma ------------------------------------------------------------------

def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM accounts ORDER BY username").fetchall()


def last_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()


def months_with_data(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(snapshot_date, 1, 7) AS m FROM profile_snapshots ORDER BY m"
    ).fetchall()
    return [r["m"] for r in rows]


def usernames_with_data(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT username FROM profile_snapshots
        WHERE snapshot_date BETWEEN ? AND ? ORDER BY username
    """, (start, end)).fetchall()
    return [r["username"] for r in rows]


def account_info(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()


def account_by_ig_id(conn: sqlite3.Connection, ig_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE ig_id = ?", (ig_id,)).fetchone()


def rename_account(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Kullanıcı adı değişen hesabın tüm geçmişini yeni ada taşır (aynı ig_id)."""
    if old == new:
        return
    conn.execute("UPDATE OR IGNORE profile_snapshots SET username = ? WHERE username = ?", (new, old))
    conn.execute("DELETE FROM profile_snapshots WHERE username = ?", (old,))  # aynı güne çakışan eski satırlar
    conn.execute("UPDATE media SET username = ? WHERE username = ?", (new, old))
    old_row = conn.execute("SELECT * FROM accounts WHERE username = ?", (old,)).fetchone()
    if old_row:
        conn.execute("""
            INSERT INTO accounts (username, ig_id, name, first_seen, last_ok, last_error)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(username) DO UPDATE SET
                first_seen = MIN(accounts.first_seen, excluded.first_seen),
                ig_id = COALESCE(accounts.ig_id, excluded.ig_id)
        """, (new, old_row["ig_id"], old_row["name"], old_row["first_seen"], old_row["last_ok"]))
        conn.execute("DELETE FROM accounts WHERE username = ?", (old,))


def profile_series(conn: sqlite3.Connection, username: str, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT * FROM profile_snapshots
        WHERE username = ? AND snapshot_date BETWEEN ? AND ?
        ORDER BY snapshot_date
    """, (username, start, end)).fetchall()


def last_profile_before(conn: sqlite3.Connection, username: str, start: str) -> sqlite3.Row | None:
    return conn.execute("""
        SELECT * FROM profile_snapshots
        WHERE username = ? AND snapshot_date < ?
        ORDER BY snapshot_date DESC LIMIT 1
    """, (username, start)).fetchone()


def first_profile_date(conn: sqlite3.Connection, username: str) -> str | None:
    """Hesabın izlenmeye başladığı gün (ilk ölçüm)."""
    row = conn.execute("SELECT MIN(snapshot_date) AS d FROM profile_snapshots WHERE username = ?", (username,)).fetchone()
    return row["d"] if row else None


def latest_profile(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("""
        SELECT * FROM profile_snapshots WHERE username = ?
        ORDER BY snapshot_date DESC LIMIT 1
    """, (username,)).fetchone()


def media_for_account(conn: sqlite3.Connection, username: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM media WHERE username = ?", (username,)).fetchall()


def media_snapshots_in_range(conn: sqlite3.Connection, username: str, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT ms.* FROM media_snapshots ms
        JOIN media m ON m.media_id = ms.media_id
        WHERE m.username = ? AND ms.snapshot_date BETWEEN ? AND ?
        ORDER BY ms.media_id, ms.snapshot_date
    """, (username, start, end)).fetchall()


def media_baseline_before(conn: sqlite3.Connection, username: str, start: str) -> dict[str, sqlite3.Row]:
    """Her gönderi için ay başından önceki son snapshot."""
    rows = conn.execute("""
        SELECT ms.* FROM media_snapshots ms
        JOIN media m ON m.media_id = ms.media_id
        WHERE m.username = ? AND ms.snapshot_date < ?
          AND ms.snapshot_date = (
              SELECT MAX(x.snapshot_date) FROM media_snapshots x
              WHERE x.media_id = ms.media_id AND x.snapshot_date < ?
          )
    """, (username, start, start)).fetchall()
    return {r["media_id"]: r for r in rows}


def media_count_tracked(conn: sqlite3.Connection, username: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM media WHERE username = ?", (username,)).fetchone()[0]


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


# --- takip tamamlanma ----------------------------------------------------------

def completed_ids(conn: sqlite3.Connection, username: str) -> set[str]:
    return {r["media_id"] for r in conn.execute(
        "SELECT media_id FROM media WHERE username = ? AND completed_at IS NOT NULL", (username,))}


def active_media(conn: sqlite3.Connection, username: str, published_since: str) -> list[sqlite3.Row]:
    """Henüz tamamlanmamış, izleme ufku içindeki gönderiler."""
    return conn.execute("""
        SELECT media_id, published_at, content_type FROM media
        WHERE username = ? AND completed_at IS NULL AND published_at >= ?
    """, (username, published_since)).fetchall()


def last_snapshots(conn: sqlite3.Connection, media_id: str, n: int = 3) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT snapshot_date, view_count, like_count FROM media_snapshots
        WHERE media_id = ? ORDER BY snapshot_date DESC LIMIT ?
    """, (media_id, n)).fetchall()


def mark_completed(conn: sqlite3.Connection, media_id: str, day: str) -> None:
    conn.execute("UPDATE media SET completed_at = ? WHERE media_id = ? AND completed_at IS NULL", (day, media_id))


def prune_completed(conn: sqlite3.Connection, before_date: str) -> int:
    """Tamamlanmış gönderilerin before_date'ten eski ARA ölçümlerini siler.

    Korunanlar: gönderinin ilk ölçümü, her takvim ayındaki son ölçümü (ay sonu değeri) ve son ölçümü
    (tamamlanma değeri). Böylece aylık toplamlar ve ilk gün / ay sonu / güncel değerleri değişmez.
    """
    cur = conn.execute("""
        DELETE FROM media_snapshots WHERE rowid IN (
            SELECT ms.rowid FROM media_snapshots ms
            JOIN media m ON m.media_id = ms.media_id
            WHERE m.completed_at IS NOT NULL AND ms.snapshot_date < ?
              AND ms.snapshot_date <> (SELECT MIN(x.snapshot_date) FROM media_snapshots x WHERE x.media_id = ms.media_id)
              AND ms.snapshot_date <> (SELECT MAX(x.snapshot_date) FROM media_snapshots x WHERE x.media_id = ms.media_id)
              AND ms.snapshot_date <> (SELECT MAX(x.snapshot_date) FROM media_snapshots x
                                       WHERE x.media_id = ms.media_id
                                         AND substr(x.snapshot_date, 1, 7) = substr(ms.snapshot_date, 1, 7))
        )
    """, (before_date,))
    return cur.rowcount
