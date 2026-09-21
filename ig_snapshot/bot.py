"""Telegram sohbet botu: uzun yoklama (long polling) ile gelen soruları cevaplar.

Yalnızca .env'deki TELEGRAM_CHAT_ID'den gelen mesajlara yanıt verir. Sürekli çalışır;
Görev Zamanlayıcı'da oturum açılışında başlatılır (scripts/register_bot.ps1).
"""
from __future__ import annotations

import logging
import msvcrt
import time
from datetime import datetime, timedelta

import requests

from . import config, db, notify, queries
from .api import GraphClient

log = logging.getLogger(__name__)
POLL_TIMEOUT = 30


def _token_info_cached():
    """Token bilgisini günde bir kez yenile (her soruda API'ye gitmemek için)."""
    cache = {"at": None, "info": None}

    def get():
        now = datetime.now()
        if cache["at"] is None or now - cache["at"] > timedelta(hours=12):
            try:
                cache["info"] = notify.token_status(GraphClient(config.ACCESS_TOKEN, config.GRAPH_VERSION))
            except Exception:  # noqa: BLE001
                cache["info"] = None
            cache["at"] = now
        return cache["info"]
    return get


def _single_instance():
    """Aynı anda ikinci bir bot kopyasını engeller (Telegram tek dinleyiciye izin verir).
    Kilit, süreç ölünce işletim sistemince otomatik bırakılır."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(config.DATA_DIR / "bot.lock", "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("Bot zaten çalışıyor; ikinci kopya kapatıldı.")
    return fh


def run_bot() -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        raise SystemExit("Telegram ayarlı değil (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Önce `telegram-test` çalıştır.")
    _lock = _single_instance()  # noqa: F841 — süreç yaşadıkça tutulmalı
    allowed = str(config.TELEGRAM_CHAT_ID)
    token_info = _token_info_cached()
    offset = None
    failures = 0
    log.info("Bot başladı; chat %s dinleniyor", allowed)
    while True:
        try:
            payload = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            updates = notify._post("getUpdates", payload, timeout=POLL_TIMEOUT + 15)
            if failures:
                log.info("Telegram bağlantısı geri geldi (%d denemeden sonra)", failures)
            failures = 0
        except (requests.RequestException, RuntimeError) as exc:
            failures += 1
            wait = min(15 * failures, 300)  # ağ kesintisinde logu boğmamak için artan bekleme (en fazla 5 dk)
            if failures <= 3 or failures % 20 == 0:
                log.warning("getUpdates hatası (%d): %s — %d sn sonra tekrar",
                            failures, notify.mask(f"{type(exc).__name__}: {exc}")[:160], wait)
            time.sleep(wait)
            continue
        for upd in updates or []:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id"))
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            if chat_id != allowed:
                log.warning("Yetkisiz sohbetten mesaj yok sayıldı: %s", chat_id)
                continue
            log.info("Soru: %s", text)
            try:
                conn = db.connect()
                try:
                    replies = queries.handle(conn, text, token_info())
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                log.exception("Cevap üretilemedi")
                replies = [f"⚠️ Cevap üretilemedi: {notify.esc(str(exc))[:200]}"]
            for reply in replies:
                notify.send(reply, chat_id)
