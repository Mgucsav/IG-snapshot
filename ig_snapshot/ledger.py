"""Günlük / haftalık / aylık CSV defteri — ham kaydın insan okunur, değişmez kopyası.

    data/gunluk/2026-11-07.csv           o günün gönderi ölçümleri (bir kez yazılır)
    data/gunluk/ozet/2026-11-07.csv      o günün hesap özeti
    data/haftalik/2026-W45.csv           Pzt→Paz kümülatif hesap özeti (her gece güncellenir)
    data/haftalik/gonderi/2026-W45.csv   o hafta yayınlanan içerikler, güncel değerleriyle
    data/aylik/2026-11.csv               ay başından bugüne kümülatif hesap özeti
    data/aylik/gonderi/2026-11.csv       o ay yayınlanan içerikler, güncel değerleriyle

Kurallar:
  • Günlük dosya yalnızca o gün ÖLÇÜLEN gönderileri içerir; ömrü dolan gönderi son ölçüldüğü günün
    dosyasında "ömrü doldu" damgasıyla görünür, sonraki günlerde yer almaz.
  • Haftalık dosya Pazartesi, aylık dosya ayın 1'inde sıfırdan başlar (yeni dosya). Hafta ay sınırını
    aşabilir; ikisi birbirinden bağımsızdır.
  • Biten dönem, içinde hâlâ izlenen gönderi kaldığı sürece güncellenmeye devam eder; hepsi ömrünü
    tamamlayınca dosya kendiliğinden sabitlenir.
"""
from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path

from . import config, db
from .content import group_of
from .daily_report import load_posts
from .queries import period_summary, posts_in_range
from .report import month_bounds, prev_month

log = logging.getLogger(__name__)

GUNLUK_DIR = "gunluk"
HAFTALIK_DIR = "haftalik"
AYLIK_DIR = "aylik"

GUNLUK_BASLIK = ["tarih", "hesap", "gonderi_id", "grup", "tur", "yayin_zamani",
                 "izlenme_toplam", "izlenme_artis", "begeni_toplam", "begeni_artis",
                 "yorum_toplam", "yorum_artis", "durum", "link"]
GONDERILER_BASLIK = ["gonderi_id", "hesap", "media_type", "media_product_type", "tur", "grup",
                     "yayin_zamani", "yayin_ayi", "ilk_goruldu", "omur_bitis", "aciklama", "link"]
GUNLUK_OZET_BASLIK = ["tarih", "hesap", "takipci", "takip_edilen", "toplam_gonderi", "takipci_artis",
                      "paylasim", "reels", "feed",
                      "izlenme_artis", "begeni_artis", "begeni_artis_reels", "begeni_artis_feed",
                      "yorum_artis", "gun_icerikleri_izlenme", "gun_icerikleri_begeni", "olculen_gonderi"]
DONEM_OZET_BASLIK = ["donem", "baslangic", "bitis", "guncelleme", "olculen_gun", "hesap",
                     "takipci_bas", "takipci_son", "takipci_degisim", "paylasim", "reels", "feed",
                     "donem_icerikleri_izlenme", "donem_icerikleri_begeni", "donem_icerikleri_yorum",
                     "kazanilan_izlenme", "kazanilan_begeni", "kazanilan_begeni_reels",
                     "kazanilan_begeni_feed", "kazanilan_yorum", "ort_izlenme_reels", "ort_begeni_feed"]
DONEM_GONDERI_BASLIK = ["donem", "hesap", "gonderi_id", "grup", "tur", "yayin_zamani",
                        "guncel_izlenme", "guncel_begeni", "guncel_yorum",
                        "ilk_gun_izlenme", "ilk_gun_begeni", "son_olcum", "durum", "link"]


def _yaz(path: Path, baslik: list[str], satirlar: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(baslik)
        w.writerows(satirlar)
    return path


def _ts(value: str | None) -> str:
    return (value or "")[:19].replace("T", " ")


def week_bounds(any_day: date) -> tuple[date, date]:
    start = any_day - timedelta(days=any_day.weekday())
    return start, start + timedelta(days=6)


def week_key(start: date) -> str:
    y, w, _ = start.isocalendar()
    return f"{y}-W{w:02d}"


# --- günlük ---------------------------------------------------------------------

def _daily_rows(conn, day: date):
    """O gün ölçülen her gönderi + bir önceki ölçüme göre artışı."""
    iso = day.isoformat()
    # Hesabın takibe alındığı ilk gün — ölçümlerden türetilir (accounts tablosuna bağlı değil)
    tracked = {r["username"]: r["ilk"] for r in conn.execute(
        "SELECT username, MIN(snapshot_date) AS ilk FROM profile_snapshots GROUP BY username")}
    rows = conn.execute("""
        SELECT m.username, m.media_id, m.content_type, m.published_at, m.permalink, m.completed_at,
               s.view_count v, s.like_count l, s.comments_count c,
               (SELECT view_count     FROM media_snapshots p WHERE p.media_id=s.media_id AND p.snapshot_date<s.snapshot_date ORDER BY p.snapshot_date DESC LIMIT 1) pv,
               (SELECT like_count     FROM media_snapshots p WHERE p.media_id=s.media_id AND p.snapshot_date<s.snapshot_date ORDER BY p.snapshot_date DESC LIMIT 1) pl,
               (SELECT comments_count FROM media_snapshots p WHERE p.media_id=s.media_id AND p.snapshot_date<s.snapshot_date ORDER BY p.snapshot_date DESC LIMIT 1) pc
        FROM media_snapshots s JOIN media m ON m.media_id = s.media_id
        WHERE s.snapshot_date = ? ORDER BY m.username, m.published_at
    """, (iso,)).fetchall()

    def artis(cur, prev, username, published):
        """İlk ölçüm: takip başladıktan sonra yayınlandıysa tamamı kazanım, yoksa başlangıç kabul edilir."""
        if cur is None:
            return None
        if prev is not None:
            return max(0, cur - prev)
        baslangic = tracked.get(username)
        return cur if (baslangic and (published or "")[:10] >= baslangic) else None

    out = []
    for r in rows:
        out.append({
            "username": r["username"], "media_id": r["media_id"],
            "content_type": r["content_type"], "published_at": r["published_at"],
            "permalink": r["permalink"], "completed_at": r["completed_at"],
            "v": r["v"], "l": r["l"], "c": r["c"],
            "gv": artis(r["v"], r["pv"], r["username"], r["published_at"]),
            "gl": artis(r["l"], r["pl"], r["username"], r["published_at"]),
            "gc": artis(r["c"], r["pc"], r["username"], r["published_at"]),
        })
    return out


def write_daily(conn, day: date) -> list[Path]:
    iso = day.isoformat()
    rows = _daily_rows(conn, day)
    if not rows:
        return []

    satir = [[iso, r["username"], r["media_id"], group_of(r["content_type"]), r["content_type"],
              _ts(r["published_at"]), r["v"], r["gv"], r["l"], r["gl"], r["c"], r["gc"],
              "ömrü doldu" if r["completed_at"] == iso else "aktif", r["permalink"]]
             for r in rows]
    p1 = _yaz(config.DATA_DIR / GUNLUK_DIR / f"{iso}.csv", GUNLUK_BASLIK, satir)

    # hesap özeti
    profil = {r["username"]: r for r in conn.execute(
        "SELECT * FROM profile_snapshots WHERE snapshot_date = ?", (iso,))}
    ozet = []
    for username in sorted({r["username"] for r in rows} | set(profil)):
        mine = [r for r in rows if r["username"] == username]
        bugun = [r for r in mine if (r["published_at"] or "")[:10] == iso]
        prev = db.last_profile_before(conn, username, iso)
        pr = profil.get(username)
        takipci = pr["followers_count"] if pr else None
        delta = (takipci - prev["followers_count"]) if (takipci is not None and prev and prev["followers_count"] is not None) else None
        topla = lambda src, k, grup=None: sum(  # noqa: E731
            (r[k] or 0) for r in src if grup is None or group_of(r["content_type"]) == grup)
        ozet.append([iso, username, takipci,
                     pr["follows_count"] if pr else None, pr["media_count"] if pr else None, delta,
                     len(bugun), sum(1 for r in bugun if group_of(r["content_type"]) == "Reels"),
                     sum(1 for r in bugun if group_of(r["content_type"]) == "Feed"),
                     topla(mine, "gv"), topla(mine, "gl"),
                     topla(mine, "gl", "Reels"), topla(mine, "gl", "Feed"), topla(mine, "gc"),
                     topla(bugun, "v"), topla(bugun, "l"), len(mine)])
    p2 = _yaz(config.DATA_DIR / GUNLUK_DIR / "ozet" / f"{iso}.csv", GUNLUK_OZET_BASLIK, ozet)
    return [p1, p2]


# --- dönem (hafta / ay) ----------------------------------------------------------

def write_catalog(conn) -> Path:
    """data/gonderiler.csv — tüm gönderilerin künyesi (açıklama, link, tür). Yeniden kurma için gerekli."""
    rows = conn.execute("""
        SELECT media_id, username, media_type, media_product_type, content_type, published_at,
               published_month, first_seen, completed_at, caption, permalink
        FROM media ORDER BY username, published_at
    """).fetchall()
    satir = [[r["media_id"], r["username"], r["media_type"], r["media_product_type"], r["content_type"],
              group_of(r["content_type"]), _ts(r["published_at"]), r["published_month"],
              _ts(r["first_seen"]), r["completed_at"],
              r["caption"], r["permalink"]] for r in rows]   # açıklama olduğu gibi (satır sonları dahil):
             # künye yedeğin kaynağıdır, birebir geri yüklenebilmeli. CSV tırnaklı alanda çok satırı taşır.
    return _yaz(config.DATA_DIR / "gonderiler.csv", GONDERILER_BASLIK, satir)


def _period_rows(conn, etiket: str, start: date, end: date, bugun: date):
    """Dönem için hesap özeti ve gönderi detayı satırları."""
    usernames = list(config.load_account_groups()) or [a["username"] for a in db.list_accounts(conn)]
    bitis = min(end, bugun)
    ozet, gonderiler = [], []
    for u in usernames:
        s = period_summary(conn, u, start, bitis)
        posts = [p for p in posts_in_range(conn, [u], start, bitis, "all")]
        if not s and not posts:
            continue
        reels = [p for p in posts if p.group == "Reels" and p.last_views is not None]
        feed = [p for p in posts if p.group == "Feed" and p.last_likes is not None]
        kohort_v = sum(p.last_views or 0 for p in posts)
        kohort_l = sum(p.last_likes or 0 for p in posts)
        kohort_c = sum(p.last_comments or 0 for p in posts)
        if s:
            ozet.append([etiket, start.isoformat(), end.isoformat(), bugun.isoformat(), s.days, u,
                         s.followers_start, s.followers_end, s.followers_delta,
                         s.posts_reels + s.posts_feed, s.posts_reels, s.posts_feed,
                         kohort_v, kohort_l, kohort_c,
                         s.views, s.likes_reels + s.likes_feed, s.likes_reels, s.likes_feed, s.comments,
                         round(sum(p.last_views for p in reels) / len(reels)) if reels else None,
                         round(sum(p.last_likes for p in feed) / len(feed)) if feed else None])
        for p in sorted(posts, key=lambda p: p.published_at):
            gonderiler.append([etiket, u, p.media_id, p.group, p.content_type, _ts(p.published_at),
                               p.last_views, p.last_likes, p.last_comments,
                               p.first_views, p.first_likes, p.last_date,
                               "ömrü doldu" if _completed(conn, p.media_id) else "izleniyor", p.permalink])
    return ozet, gonderiler


def _completed(conn, media_id: str) -> bool:
    row = conn.execute("SELECT completed_at FROM media WHERE media_id = ?", (media_id,)).fetchone()
    return bool(row and row["completed_at"])


def write_period(conn, dirname: str, etiket: str, start: date, end: date, bugun: date) -> list[Path]:
    ozet, gonderiler = _period_rows(conn, etiket, start, end, bugun)
    if not ozet and not gonderiler:
        return []
    out = [_yaz(config.DATA_DIR / dirname / f"{etiket}.csv", DONEM_OZET_BASLIK, ozet)]
    if gonderiler:
        out.append(_yaz(config.DATA_DIR / dirname / "gonderi" / f"{etiket}.csv",
                        DONEM_GONDERI_BASLIK, gonderiler))
    return out


def _active_weeks(conn, bugun: date) -> list[tuple[date, date]]:
    """Bu hafta + önceki hafta (geçen haftanın içerikleri hâlâ büyüyor olabilir)."""
    bu = week_bounds(bugun)
    onceki = week_bounds(bugun - timedelta(days=7))
    return [bu, onceki]


def _active_months(conn, bugun: date) -> list[str]:
    """Bu ay + önceki ay + hâlâ izlenen gönderisi olan aylar (ölçüm verisi olanlarla sınırlı)."""
    bu = bugun.strftime("%Y-%m")
    aktif = set(db.months_with_active_media(conn)) & set(db.months_with_data(conn))
    return sorted({bu, prev_month(bu), *aktif}, reverse=True)


def generate(conn=None, day: date | None = None) -> dict:
    """Günlük + haftalık + aylık defterleri yazar."""
    own = conn is None
    conn = conn or db.connect()
    day = day or date.today()
    yazilan: dict[str, list[Path]] = {"gunluk": [], "kunye": [], "haftalik": [], "aylik": []}
    try:
        yazilan["gunluk"] = write_daily(conn, day)
        yazilan["kunye"] = [write_catalog(conn)]
        for start, end in _active_weeks(conn, day):
            yazilan["haftalik"] += write_period(conn, HAFTALIK_DIR, week_key(start), start, end, day)
        for ay in _active_months(conn, day):
            s, e = month_bounds(ay)
            yazilan["aylik"] += write_period(conn, AYLIK_DIR, ay,
                                             date.fromisoformat(s), date.fromisoformat(e), day)
    finally:
        if own:
            conn.close()
    log.info("Defter yazıldı: %d günlük, %d haftalık, %d aylık dosya",
             len(yazilan["gunluk"]), len(yazilan["haftalik"]), len(yazilan["aylik"]))
    return yazilan


# --- CSV'den veritabanını yeniden kurma -------------------------------------------

def rebuild(hedef: Path, kaynak: Path | None = None) -> dict:
    """CSV defterinden veritabanını sıfırdan kurar (yedeğin çalıştığını kanıtlar).

    Gerekli dosyalar: gonderiler.csv (künye) · gunluk/*.csv (ölçümler) · gunluk/ozet/*.csv (profil).
    """
    kaynak = kaynak or config.DATA_DIR
    if hedef.exists():
        hedef.unlink()
    conn = db.connect(hedef)
    sayac = {"gonderi": 0, "olcum": 0, "profil": 0, "hesap": 0}

    def _oku(path: Path):
        with path.open(encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")

    def _int(v):
        return int(v) if v not in (None, "") else None

    from datetime import datetime as _dt
    with conn:
        kunye = kaynak / "gonderiler.csv"
        if not kunye.exists():
            raise FileNotFoundError(f"künye dosyası yok: {kunye}")
        hesaplar = {}
        for r in _oku(kunye):
            pub = _dt.strptime(r["yayin_zamani"], "%Y-%m-%d %H:%M:%S").astimezone() if r["yayin_zamani"] else None
            db.upsert_media(conn, r["gonderi_id"], r["hesap"], r["media_type"], r["media_product_type"],
                            r["tur"], r["aciklama"], r["link"], pub)
            if r["omur_bitis"]:
                db.mark_completed(conn, r["gonderi_id"], r["omur_bitis"])
            hesaplar.setdefault(r["hesap"], None)
            sayac["gonderi"] += 1
        for h in hesaplar:
            db.upsert_account(conn, h, None, None)
            sayac["hesap"] += 1

        for path in sorted((kaynak / GUNLUK_DIR).glob("*.csv")):
            for r in _oku(path):
                db.upsert_media_snapshot(conn, date.fromisoformat(r["tarih"]), r["gonderi_id"],
                                         _int(r["begeni_toplam"]), _int(r["yorum_toplam"]),
                                         _int(r["izlenme_toplam"]))
                sayac["olcum"] += 1
        for path in sorted((kaynak / GUNLUK_DIR / "ozet").glob("*.csv")):
            for r in _oku(path):
                db.upsert_profile_snapshot(conn, date.fromisoformat(r["tarih"]), r["hesap"],
                                           _int(r["takipci"]), _int(r["takip_edilen"]),
                                           _int(r["toplam_gonderi"]))
                sayac["profil"] += 1
    conn.close()
    log.info("Yeniden kuruldu: %s — %s", hedef, sayac)
    return sayac
