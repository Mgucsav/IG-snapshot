"""Günlük snapshot: accounts.txt'deki her hesap için profil + gönderi metriklerini kaydeder."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

from . import config, db
from .api import GraphAPIError, GraphClient, RateLimited
from .content import classify

log = logging.getLogger(__name__)


def parse_timestamp(value: str | None) -> datetime | None:
    """API'nin '2026-09-15T10:22:33+0000' biçimini yerel saate çevirir."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(value, fmt).astimezone()
        except ValueError:
            continue
    log.warning("Tarih çözümlenemedi: %s", value)
    return None


def _int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def account_stats(conn, snapshot_date: date, username: str, data: dict, saved: int) -> dict:
    """Bildirim için özet: takipçi (+önceki güne göre fark), bugün atılan içerik, veri kalitesi işaretleri."""
    profile = data.get("profile") or {}
    media = data.get("media") or []
    followers = _int(profile.get("followers_count"))
    prev = db.last_profile_before(conn, username, snapshot_date.isoformat())
    delta = followers - prev["followers_count"] if followers is not None and prev and prev["followers_count"] is not None else None
    today_iso = snapshot_date.isoformat()
    reels_today = feed_today = 0
    for m in media:
        ts = parse_timestamp(m.get("timestamp"))
        if ts and ts.date().isoformat() == today_iso:
            if classify(m.get("media_type"), m.get("media_product_type")) == "Reels":
                reels_today += 1
            else:
                feed_today += 1
    reels = [m for m in media if m.get("media_product_type") == "REELS"]
    return {
        "followers": followers, "followers_delta": delta, "saved": saved,
        "posts_today": reels_today + feed_today, "reels_today": reels_today, "feed_today": feed_today,
        "reels_views_missing": bool(reels) and all(m.get("view_count") is None for m in reels),
        "likes_missing": bool(media) and all(m.get("like_count") is None for m in media),
    }


def store_account(conn, snapshot_date: date, username: str, data: dict,
                  skip_ids: set[str] | None = None) -> int:
    """Bir hesabın business_discovery çıktısını veritabanına yazar; kaydedilen gönderi sayısını döner.
    skip_ids: takibi tamamlanmış gönderiler — değerleri tamamlandığı günde dondurulur, yeni ölçüm yazılmaz."""
    profile = data.get("profile") or {}
    skip_ids = skip_ids or set()
    db.upsert_account(conn, username, profile.get("id"), profile.get("name"))
    db.upsert_profile_snapshot(
        conn, snapshot_date, username,
        _int(profile.get("followers_count")),
        _int(profile.get("follows_count")),
        _int(profile.get("media_count")),
    )
    count = 0
    for m in data.get("media") or []:
        media_id = m.get("id")
        if not media_id or media_id in skip_ids:
            continue
        db.upsert_media(
            conn, media_id, username,
            m.get("media_type"), m.get("media_product_type"),
            classify(m.get("media_type"), m.get("media_product_type")),
            m.get("caption"), m.get("permalink"), parse_timestamp(m.get("timestamp")),
        )
        db.upsert_media_snapshot(
            conn, snapshot_date, media_id,
            _int(m.get("like_count")), _int(m.get("comments_count")), _int(m.get("view_count")),
        )
        count += 1
    return count


def evaluate_completion(conn, username: str, snapshot_date: date, cutoff: str) -> int:
    """Günlük artışı sönen gönderileri 'tamamlandı' işaretler; işaretlenen sayısını döner.

    Kural: en az MIN_TRACK_DAYS günlük ölçüm varken, son günün artışı bir önceki günün artışının
    STOP_RATIO katından azsa (ya da iki gündür hiç artış yoksa). Reels'te izlenme, Feed'de beğeni esas alınır.
    """
    done = 0
    today = snapshot_date.isoformat()
    for m in db.active_media(conn, username, cutoff):
        snaps = db.last_snapshots(conn, m["media_id"], 3)
        if len(snaps) < 3 or snaps[0]["snapshot_date"] != today:
            continue
        published = (m["published_at"] or today)[:10]
        if (snapshot_date - date.fromisoformat(published)).days < config.MIN_TRACK_DAYS:
            continue
        key = "view_count" if any(s["view_count"] is not None for s in snaps) else "like_count"
        v0, v1, v2 = (s[key] for s in snaps)
        if None in (v0, v1, v2):
            continue
        gain_today, gain_prev = v0 - v1, v1 - v2
        if (gain_prev > 0 and gain_today < config.STOP_RATIO * gain_prev) or (gain_prev <= 0 and gain_today <= 0):
            db.mark_completed(conn, m["media_id"], today)
            done += 1
    if done:
        log.info("@%s — %d gönderinin takibi tamamlandı", username, done)
    return done


def prune_old(conn, snapshot_date: date) -> int:
    """Tamamlanmış gönderilerin eski ara ölçümlerini budar; çok satır silindiyse dosyayı sıkıştırır."""
    before = (snapshot_date - timedelta(days=config.PRUNE_AFTER_DAYS)).isoformat()
    with conn:
        removed = db.prune_completed(conn, before)
    if removed:
        log.info("Budama: %s öncesi %d ara ölçüm silindi", before, removed)
        if removed >= 5000:
            conn.execute("VACUUM")
    return removed


def usage_peak(client: GraphClient) -> float:
    """Uygulama (1 saatlik) kullanım yüzdelerinin en yükseği."""
    u = client.usage_summary()
    return max((v for k, v in u.items() if k.startswith("app.") and isinstance(v, (int, float))), default=0)


def wait_for_capacity(client: GraphClient) -> int:
    """Kullanım eşiği aşıldıysa pencere kayana kadar bekle; beklenen saniyeyi döner."""
    waited = 0
    while usage_peak(client) >= config.USAGE_PAUSE_PCT and waited < 6 * 3600:
        log.warning("API kullanımı %%%.0f ≥ %%%.0f — %d sn bekleniyor", usage_peak(client),
                    config.USAGE_PAUSE_PCT, config.USAGE_SLEEP_SEC)
        time.sleep(config.USAGE_SLEEP_SEC)
        waited += config.USAGE_SLEEP_SEC
        # pencerenin kaydığını görmek için ucuz bir çağrı
        try:
            client.get("me", {"fields": "id"})
        except GraphAPIError:
            pass
    return waited


def run_snapshot(snapshot_date: date | None = None) -> dict:
    """Tüm hesapları çeker. Sonuç: {'ok': [...], 'failed': {username: hata}, 'date': date}."""
    if not config.ACCESS_TOKEN:
        raise SystemExit(".env içinde IG_ACCESS_TOKEN tanımlı değil. Önce `python -m ig_snapshot check` çalıştır.")
    if not config.IG_USER_ID:
        raise SystemExit(".env içinde IG_USER_ID yok. `python -m ig_snapshot check` komutu otomatik bulur.")

    accounts = config.load_accounts()
    if not accounts:
        raise SystemExit(f"{config.ACCOUNTS_FILE.name} boş. Takip edilecek kullanıcı adlarını ekle.")

    snapshot_date = snapshot_date or date.today()
    client = GraphClient(config.ACCESS_TOKEN, config.GRAPH_VERSION)
    conn = db.connect()
    started_at = datetime.now().isoformat(timespec="seconds")
    ok: list[str] = []
    failed: dict[str, str] = {}
    stats: dict[str, dict] = {}
    waited = 0
    t0 = time.time()

    cutoff = config.track_cutoff()
    log.info("Snapshot başlıyor: %s — %d hesap, %s sonrası gönderiler (%d gün)",
             snapshot_date, len(accounts), cutoff, config.TRACK_DAYS)
    for i, username in enumerate(accounts, 1):
        try:
            completed = db.completed_ids(conn, username)
            data = client.business_discovery(
                config.IG_USER_ID, username,
                page_size=config.MEDIA_PAGE_SIZE, max_media=config.MEDIA_MAX,
                stop_before=cutoff, completed_ids=completed,
            )
            with conn:
                stats[username] = account_stats(conn, snapshot_date, username, data, 0)  # takipçi farkı için önce
                n = store_account(conn, snapshot_date, username, data, skip_ids=completed)
                stats[username]["saved"] = n
                evaluate_completion(conn, username, snapshot_date, cutoff)
            prof = data.get("profile") or {}
            log.info("[%d/%d] @%s — takipçi %s, %d gönderi kaydedildi",
                     i, len(accounts), username, prof.get("followers_count"), n)
            ok.append(username)
        except RateLimited as exc:
            failed[username] = str(exc)
            log.error("[%d/%d] @%s — rate limit aşıldı, kalan hesaplar atlanıyor: %s", i, len(accounts), username, exc)
            with conn:
                db.mark_account_error(conn, username, str(exc))
            for rest in accounts[i:]:
                failed[rest] = "rate limit nedeniyle atlandı"
            break
        except GraphAPIError as exc:
            failed[username] = str(exc)
            log.error("[%d/%d] @%s — hata: %s", i, len(accounts), username, exc)
            with conn:
                db.mark_account_error(conn, username, str(exc))
        if i < len(accounts):
            time.sleep(config.REQUEST_PAUSE)
            waited += wait_for_capacity(client)

    with conn:
        db.record_run(conn, started_at, len(ok), len(failed),
                      notes="; ".join(f"{u}: {e}" for u, e in failed.items())[:1000])
    prune_old(conn, snapshot_date)
    conn.close()
    log.info("Snapshot bitti: %d başarılı, %d hatalı", len(ok), len(failed))
    return {"date": snapshot_date, "ok": ok, "failed": failed, "stats": stats,
            "waited": waited, "elapsed": time.time() - t0, "client": client}
