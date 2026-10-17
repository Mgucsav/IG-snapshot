"""Isolated acceptance checks for the account-transition review.

These tests intentionally use temporary SQLite files and in-memory HTTP
responses. They never load or write the production database, .env, reports,
logs, or Telegram.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ig_snapshot import db, snapshot
from ig_snapshot.api import GraphAPIError, GraphClient


def profile_payload(username: str, ig_id: str = "target-ig-1", followers: int = 100) -> dict:
    return {
        "profile": {
            "id": ig_id,
            "username": username,
            "name": "Target",
            "followers_count": followers,
            "follows_count": 10,
            "media_count": 1,
        },
        "media": [
            {
                "id": "media-1",
                "media_type": "VIDEO",
                "media_product_type": "REELS",
                "like_count": 12,
                "comments_count": 2,
                "view_count": 250,
                "caption": "test caption",
                "permalink": "https://example.invalid/p/media-1",
                "timestamp": "2026-10-10T10:00:00+0000",
            }
        ],
    }


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 400
        self.headers: dict[str, str] = {}
        self.text = ""

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, int]] = []

    def get(self, url: str, params: dict, timeout: int) -> FakeResponse:
        self.calls.append((url, dict(params), timeout))
        return self.responses.pop(0)


class TestAccountTransitionAcceptance(unittest.TestCase):
    def test_target_history_survives_username_change_in_real_storage_path(self) -> None:
        """Supporting check: same target IG id is merged under a changed handle."""
        with tempfile.TemporaryDirectory(prefix="ig-transition-") as td:
            path = Path(td) / "review.db"
            conn = db.connect(path)
            with conn:
                snapshot.store_account(conn, date(2026, 10, 15), "old_handle", profile_payload("old_handle"))
            with conn:
                snapshot.store_account(conn, date(2026, 10, 16), "new_handle", profile_payload("new_handle", followers=101))

            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
            self.assertIsNotNone(db.account_info(conn, "new_handle"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_snapshots").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_snapshots").fetchone()[0], 2)
            self.assertEqual(db.account_info(conn, "new_handle")["ig_id"], "target-ig-1")
            conn.close()

    def test_isolated_new_snapshot_and_repeat_are_idempotent(self) -> None:
        """T03/T04: a temporary database gets one logical row per key."""
        with tempfile.TemporaryDirectory(prefix="ig-transition-") as td:
            conn = db.connect(Path(td) / "review.db")
            payload = profile_payload("new_handle")
            with conn:
                first = snapshot.store_account(conn, date(2026, 10, 17), "new_handle", payload)
            with conn:
                second = snapshot.store_account(conn, date(2026, 10, 17), "new_handle", payload)

            self.assertEqual(first, 1)
            self.assertEqual(second, 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM profile_snapshots").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_snapshots").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media").fetchone()[0], 1)
            conn.close()

    def test_token_error_is_failed_without_zero_snapshot_or_history_loss(self) -> None:
        """T05: real run orchestration records an error and preserves prior data."""
        with tempfile.TemporaryDirectory(prefix="ig-transition-") as td:
            path = Path(td) / "review.db"
            real_connect = db.connect
            seed = real_connect(path)
            with seed:
                snapshot.store_account(seed, date(2026, 10, 17), "target", profile_payload("target"))
            seed.close()

            class FailingClient:
                def __init__(self, *args, **kwargs) -> None:
                    pass

                def business_discovery(self, *args, **kwargs):
                    raise GraphAPIError("Invalid OAuth access token", code=190, http_status=400)

                def usage_summary(self):
                    return {}

            with patch.object(snapshot.config, "ACCESS_TOKEN", "isolated-test-token"), \
                 patch.object(snapshot.config, "IG_USER_ID", "authorizing-ig"), \
                 patch.object(snapshot.config, "load_accounts", return_value=["target"]), \
                 patch.object(snapshot.config, "REQUEST_PAUSE", 0), \
                 patch.object(snapshot, "GraphClient", FailingClient), \
                 patch.object(snapshot.db, "connect", side_effect=lambda *args, **kwargs: real_connect(path)):
                result = snapshot.run_snapshot(date(2026, 10, 18))

            check = real_connect(path)
            self.assertEqual(result["ok"], [])
            self.assertIn("target", result["failed"])
            self.assertIn("token geçersiz", result["failed"]["target"])
            self.assertEqual(check.execute("SELECT COUNT(*) FROM profile_snapshots").fetchone()[0], 1)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM profile_snapshots WHERE followers_count = 0").fetchone()[0], 0)
            run = db.last_run(check)
            self.assertEqual((run["ok_count"], run["fail_count"]), (0, 1))
            self.assertIn("token geçersiz", check.execute("SELECT last_error FROM accounts").fetchone()[0])
            check.close()

    def test_business_discovery_follows_cursor_with_real_client(self) -> None:
        """T06 pagination path: the production GraphClient follows nested cursors."""
        page_one = {
            "business_discovery": {
                "id": "target-ig-1",
                "username": "target",
                "name": "Target",
                "followers_count": 100,
                "follows_count": 10,
                "media_count": 3,
                "media": {
                    "data": [
                        {"id": "m1", "timestamp": "2026-10-17T10:00:00+0000"},
                        {"id": "m2", "timestamp": "2026-10-16T10:00:00+0000"},
                    ],
                    "paging": {"cursors": {"after": "cursor-1"}},
                },
            }
        }
        page_two = {
            "business_discovery": {
                "id": "target-ig-1",
                "media": {
                    "data": [{"id": "m3", "timestamp": "2026-10-15T10:00:00+0000"}],
                    "paging": {},
                },
            }
        }
        client = GraphClient("isolated-test-token", "v25.0", timeout=1)
        fake = FakeSession([FakeResponse(page_one), FakeResponse(page_two)])
        client.session = fake
        result = client.business_discovery("authorizing-ig", "target", page_size=2, max_media=10)

        self.assertEqual([m["id"] for m in result["media"]], ["m1", "m2", "m3"])
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1][1]["fields"].count("after(cursor-1)"), 1)

    def test_sqlite_backup_restores_isolated_copy(self) -> None:
        """T08: use SQLite backup, then verify restored rows and schema."""
        with tempfile.TemporaryDirectory(prefix="ig-transition-") as td:
            source_path = Path(td) / "source.db"
            restored_path = Path(td) / "restored.db"
            source = db.connect(source_path)
            with source:
                snapshot.store_account(source, date(2026, 10, 17), "target", profile_payload("target"))
            restored = sqlite3.connect(restored_path)
            source.backup(restored)
            restored.close()
            source.close()

            check = db.connect(restored_path)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM media_snapshots").fetchone()[0], 1)
            self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            check.close()


if __name__ == "__main__":
    unittest.main()
