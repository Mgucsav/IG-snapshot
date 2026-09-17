"""Ortam değişkenleri, dosya yolları ve hesap listesi."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ACCOUNTS_FILE = ROOT / "accounts.txt"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "ig_snapshot.db"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"

load_dotenv(ENV_PATH)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().strip('"').strip("'")


ACCESS_TOKEN = _env("IG_ACCESS_TOKEN")
IG_USER_ID = _env("IG_USER_ID")
APP_ID = _env("FB_APP_ID")
APP_SECRET = _env("FB_APP_SECRET")
GRAPH_VERSION = _env("GRAPH_API_VERSION", "v25.0")
MEDIA_PAGE_SIZE = int(_env("MEDIA_PAGE_SIZE", "50"))
MEDIA_MAX = int(_env("MEDIA_MAX", "600"))                     # hesap başına üst sınır (güvenlik)
TRACK_DAYS = int(_env("TRACK_DAYS", "45"))                   # gönderi yayından sonra bu kadar gün ölçülür
REQUEST_PAUSE = float(_env("REQUEST_PAUSE", "1.5"))
USAGE_PAUSE_PCT = float(_env("USAGE_PAUSE_PCT", "70"))      # uygulama kullanımı bu yüzdeyi aşınca bekle
USAGE_SLEEP_SEC = int(_env("USAGE_SLEEP_SEC", "600"))        # bekleme süresi (1 saatlik pencere kayana kadar)
CHANNEL_LOG_DAYS = int(_env("CHANNEL_LOG_DAYS", "90"))      # kanal dosyalarındaki gönderi×gün matrisinin genişliği

# Telegram bildirimleri (boşsa bildirim gönderilmez)
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
TOKEN_WARN_DAYS = int(_env("TOKEN_WARN_DAYS", "5"))          # tokena bu kadar gün kalınca her gün uyar


def track_cutoff() -> str:
    """İzleme ufku: bu tarihten (UTC, 'YYYY-MM-DD') eski gönderiler artık çekilmez."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=TRACK_DAYS)).strftime("%Y-%m-%d")


def load_accounts() -> list[str]:
    """accounts.txt: her satırda bir kullanıcı adı; '#' sonrası yorum sayılır."""
    if not ACCOUNTS_FILE.exists():
        return []
    names: list[str] = []
    for raw in ACCOUNTS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        line = re.sub(r"^https?://(www\.)?instagram\.com/", "", line)
        name = line.strip("/").split("/")[0].lstrip("@").strip().lower()
        if name and name not in names:
            names.append(name)
    return names


def save_env_value(key: str, value: str) -> None:
    """.env içinde KEY=VALUE satırını günceller ya da sona ekler."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value
