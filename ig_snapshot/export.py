"""Ham veriyi dışa aktarma: reports/disa-aktarim/<dönem>/ altına CSV + Excel + (istenirse) veritabanı kopyası.

Amaç: ay sonunda (ya da istenen herhangi bir anda) veriyi Excel'de incelemek, birine göndermek veya arşivlemek.
Rapor dosyalarından farkı: burada hesaplama yok, veritabanındaki ham ölçümler olduğu gibi yazılır.
"""
from __future__ import annotations

import csv
import logging
import shutil
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from . import config, db
from .report import month_bounds, month_label, style_sheet

log = logging.getLogger(__name__)
OUT_DIRNAME = "disa-aktarim"
EXCEL_ROW_LIMIT = 200_000     # bu sayıyı aşan tablo sadece CSV olarak yazılır

PROFIL_BASLIK = ["olcum_tarihi", "hesap", "takipci", "takip_edilen", "toplam_gonderi", "cekim_zamani"]
GONDERI_BASLIK = ["gonderi_id", "hesap", "grup", "tur", "yayin_zamani", "yayin_ayi",
                  "nihai_izlenme", "nihai_begeni", "nihai_yorum", "son_olcum_tarihi", "aciklama", "link"]
OLCUM_BASLIK = ["olcum_tarihi", "gonderi_id", "hesap", "yayin_zamani", "grup", "tur",
                "izlenme", "begeni", "yorum"]


def _fetch(conn, month: str | None):
    """(profil satırları, gönderi satırları, ölçüm satırları) — month verilirse o aya sınırlı."""
    if month:
        start, end = month_bounds(month)
        profil = conn.execute("""
            SELECT snapshot_date, username, followers_count, follows_count, media_count, fetched_at
            FROM profile_snapshots WHERE snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date, username
        """, (start, end)).fetchall()
        gonderi = conn.execute("""
            SELECT m.*, s.view_count, s.like_count, s.comments_count, s.snapshot_date AS son_olcum
            FROM media m LEFT JOIN media_snapshots s ON s.media_id = m.media_id
                 AND s.snapshot_date = (SELECT MAX(x.snapshot_date) FROM media_snapshots x WHERE x.media_id = m.media_id)
            WHERE m.published_month = ? ORDER BY m.published_at
        """, (month,)).fetchall()
        olcum = conn.execute("""
            SELECT s.snapshot_date, s.media_id, m.username, m.published_at, m.content_type,
                   s.view_count, s.like_count, s.comments_count
            FROM media_snapshots s JOIN media m ON m.media_id = s.media_id
            WHERE s.snapshot_date BETWEEN ? AND ? ORDER BY s.snapshot_date, m.username, m.published_at
        """, (start, end)).fetchall()
    else:
        profil = conn.execute("""
            SELECT snapshot_date, username, followers_count, follows_count, media_count, fetched_at
            FROM profile_snapshots ORDER BY snapshot_date, username
        """).fetchall()
        gonderi = conn.execute("""
            SELECT m.*, s.view_count, s.like_count, s.comments_count, s.snapshot_date AS son_olcum
            FROM media m LEFT JOIN media_snapshots s ON s.media_id = m.media_id
                 AND s.snapshot_date = (SELECT MAX(x.snapshot_date) FROM media_snapshots x WHERE x.media_id = m.media_id)
            ORDER BY m.published_at
        """).fetchall()
        olcum = conn.execute("""
            SELECT s.snapshot_date, s.media_id, m.username, m.published_at, m.content_type,
                   s.view_count, s.like_count, s.comments_count
            FROM media_snapshots s JOIN media m ON m.media_id = s.media_id
            ORDER BY s.snapshot_date, m.username, m.published_at
        """).fetchall()
    return profil, gonderi, olcum


def _grup(content_type: str | None) -> str:
    return "Reels" if content_type == "Reels" else "Feed"


def _satirlar(profil, gonderi, olcum):
    p = [[r["snapshot_date"], r["username"], r["followers_count"], r["follows_count"],
          r["media_count"], r["fetched_at"]] for r in profil]
    g = [[r["media_id"], r["username"], _grup(r["content_type"]), r["content_type"],
          (r["published_at"] or "")[:19].replace("T", " "), r["published_month"],
          r["view_count"], r["like_count"], r["comments_count"], r["son_olcum"],
          (r["caption"] or "").replace("\n", " ").strip(), r["permalink"]] for r in gonderi]
    o = [[r["snapshot_date"], r["media_id"], r["username"], (r["published_at"] or "")[:19].replace("T", " "),
          _grup(r["content_type"]), r["content_type"], r["view_count"], r["like_count"], r["comments_count"]]
         for r in olcum]
    return p, g, o


def _csv_yaz(path: Path, baslik: list[str], satirlar: list[list]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(baslik)
        w.writerows(satirlar)


def _xlsx_yaz(path: Path, tablolar: list[tuple[str, list[str], list[list], list[int]]]) -> bool:
    if any(len(s) > EXCEL_ROW_LIMIT for _, _, s, _ in tablolar):
        return False
    wb = Workbook()
    ilk = True
    for ad, baslik, satirlar, genislik in tablolar:
        ws = wb.active if ilk else wb.create_sheet()
        ws.title = ad
        ilk = False
        ws.append(baslik)
        for s in satirlar:
            ws.append(s)
        style_sheet(ws, genislik)
    wb.save(path)
    return True


def generate(month: str | None = None, include_db: bool = False, conn=None) -> dict | None:
    """month='2026-09' → o ay; month=None → tüm veri. Döner: {'dir':…, 'dosyalar':[…], 'sayilar':{…}}"""
    own = conn is None
    conn = conn or db.connect()
    try:
        profil, gonderi, olcum = _fetch(conn, month)
    finally:
        if own:
            conn.close()
    if not profil and not gonderi:
        log.info("Dışa aktarma: %s için veri yok", month or "tüm veri")
        return None

    p, g, o = _satirlar(profil, gonderi, olcum)
    etiket = month or "tum-veri"
    out_dir = config.REPORTS_DIR / OUT_DIRNAME / etiket
    out_dir.mkdir(parents=True, exist_ok=True)

    dosyalar = []
    _csv_yaz(out_dir / "profil-olcumleri.csv", PROFIL_BASLIK, p); dosyalar.append("profil-olcumleri.csv")
    _csv_yaz(out_dir / "gonderiler.csv", GONDERI_BASLIK, g); dosyalar.append("gonderiler.csv")
    _csv_yaz(out_dir / "gonderi-olcumleri.csv", OLCUM_BASLIK, o); dosyalar.append("gonderi-olcumleri.csv")

    xlsx = out_dir / f"veri-{etiket}.xlsx"
    if _xlsx_yaz(xlsx, [
        ("Profil ölçümleri", PROFIL_BASLIK, p, [13, 18, 11, 12, 13, 18]),
        ("Gönderiler", GONDERI_BASLIK, g, [20, 18, 8, 10, 18, 10, 13, 12, 10, 14, 60, 45]),
        ("Gönderi ölçümleri", OLCUM_BASLIK, o, [13, 20, 18, 18, 8, 10, 11, 10, 9]),
    ]):
        dosyalar.append(xlsx.name)
    else:
        log.info("Excel atlandı (satır sayısı %d üstü); CSV dosyaları yazıldı", EXCEL_ROW_LIMIT)

    if include_db:
        hedef = out_dir / f"ig_snapshot-{datetime.now():%Y%m%d}.db"
        shutil.copy2(config.DB_PATH, hedef)          # SQLite dosya kopyası = tam yedek
        dosyalar.append(hedef.name)

    (out_dir / "OKUBENI.txt").write_text(_aciklama(etiket, len(p), len(g), len(o)), encoding="utf-8")
    dosyalar.append("OKUBENI.txt")
    log.info("Dışa aktarma: %s (%d profil, %d gönderi, %d ölçüm satırı)", out_dir, len(p), len(g), len(o))
    return {"dir": out_dir, "dosyalar": dosyalar,
            "sayilar": {"profil": len(p), "gonderi": len(g), "olcum": len(o)}}


def _aciklama(etiket: str, np_: int, ng: int, no: int) -> str:
    donem = month_label(etiket) if etiket != "tum-veri" else "tüm veri"
    return f"""IG Snapshot — ham veri dışa aktarımı
Dönem: {donem}          Oluşturulma: {datetime.now():%d.%m.%Y %H:%M}

DOSYALAR
  profil-olcumleri.csv    {np_} satır — her hesap için her günün takipçi/gönderi sayısı
      olcum_tarihi · hesap · takipci · takip_edilen · toplam_gonderi · cekim_zamani

  gonderiler.csv          {ng} satır — dönemde YAYINLANAN gönderiler ve ulaştıkları son değerler
      gonderi_id · hesap · grup (Reels/Feed) · tur · yayin_zamani · yayin_ayi
      nihai_izlenme · nihai_begeni · nihai_yorum · son_olcum_tarihi · aciklama · link

  gonderi-olcumleri.csv   {no} satır — GÜN GÜN ham ölçümler (asıl veri)
      olcum_tarihi · gonderi_id · hesap · yayin_zamani · grup · tur · izlenme · begeni · yorum

  veri-{etiket}.xlsx      aynı üç tablo tek Excel dosyasında (satır sayısı sığarsa)

NOTLAR
  • CSV dosyaları noktalı virgül (;) ayraçlı ve UTF-8 BOM'ludur; Excel'de çift tıklayınca doğru açılır.
  • Fotoğraf ve carousel içeriklerinde izlenme boştur (Instagram API bu veriyi vermez).
  • Bir gönderi, izlenmesi artmayı bırakana kadar her gece ölçülür; sonra son değeri nihai kabul edilir.
  • Saatler yerel (Türkiye) saatidir.
"""
