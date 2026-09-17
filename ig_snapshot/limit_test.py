"""Limit testi: örnek hesaplarla çağrı maliyetini ve Meta kullanım yüzdelerini ölçüp
tek seferde / günde kaç hesabın güvenle takip edilebileceğini tahmin eder."""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

from . import config
from .api import GraphAPIError, GraphClient, RateLimited
from .snapshot import parse_timestamp

SAFETY = 0.5          # tek seferlik çalışmada limitin en fazla bu kadarını kullanmayı hedefle
STOP_AT_PCT = 60      # yük testinde kullanım bu yüzdeyi geçerse dur

APP_METRICS = (("app.call_count", "çağrı sayısı"),
               ("app.total_time", "toplam süre"),
               ("app.total_cputime", "CPU süresi"))


def _pct(v) -> str:
    return "—" if v is None else f"%{v:g}"


def _usage_line(u: dict) -> str:
    return (f"app(1 saat): çağrı {_pct(u['app.call_count'])} süre {_pct(u['app.total_time'])} "
            f"cpu {_pct(u['app.total_cputime'])} · buc(24 saat): çağrı {_pct(u['buc.call_count'])}"
            + (f" · erişim {u['buc.regain_min']} dk sonra" if u.get("buc.regain_min") else ""))


def run(usernames: list[str], burst_calls: int = 20) -> int:
    if not config.ACCESS_TOKEN or not config.IG_USER_ID:
        print("Önce `check` komutunu çalıştır (token + IG_USER_ID gerekli).")
        return 1
    client = GraphClient(config.ACCESS_TOKEN, config.GRAPH_VERSION)
    now = datetime.now(timezone.utc)

    # 1) Her hesap için gerçek günlük çekimin maliyeti
    print(f"1) Hesap başına maliyet (TRACK_DAYS={config.TRACK_DAYS}, MEDIA_MAX={config.MEDIA_MAX}, sayfa={config.MEDIA_PAGE_SIZE})")
    print(f"   {'Hesap':<22}{'Takipçi':>12}{'Gönderi':>9}{'Çekilen':>9}{'Çağrı':>7}{'Süre':>7}"
          f"{'Paylaşım/gün':>14}{'50 gönderi≈':>13}")
    rows = []
    for u in usernames:
        t0 = time.time()
        c0 = client.calls
        try:
            data = client.business_discovery(config.IG_USER_ID, u,
                                             page_size=config.MEDIA_PAGE_SIZE, max_media=config.MEDIA_MAX,
                                             stop_before=config.track_cutoff())
        except GraphAPIError as exc:
            print(f"   @{u:<21} HATA: {exc}")
            continue
        calls, secs = client.calls - c0, time.time() - t0
        prof, media = data["profile"], data["media"]
        stamps = [parse_timestamp(m.get("timestamp")) for m in media]
        stamps = [s.astimezone(timezone.utc) for s in stamps if s]
        recent30 = sum(1 for s in stamps if (now - s).days < 30)
        span = ((max(stamps) - min(stamps)).total_seconds() / 86400) if len(stamps) > 1 else 0
        # çekilen gönderiler 30 günü kapsamıyorsa (yoğun hesap) gerçek aralığa böl
        per_day = len(stamps) / span if 0 < span < 30 else recent30 / 30
        cover50 = (50 / per_day) if per_day else None
        rows.append({"username": u, "calls": calls, "secs": secs, "per_day": per_day, "fetched": len(media)})
        print(f"   @{u:<21}{(prof.get('followers_count') or 0):>12,}{(prof.get('media_count') or 0):>9,}"
              f"{len(media):>9}{calls:>7}{secs:>6.1f}s{per_day:>14.1f}"
              f"{(f'{cover50:.0f} gün' if cover50 else '—'):>13}")
        print(f"      {_usage_line(client.usage_summary())}")
    if not rows:
        return 1

    # 2) Yük testi: kısa aralıklı ardışık çağrılarla yüzde artışını ölç
    print(f"\n2) Yük testi: {burst_calls} ardışık çağrı (tek sayfa = 50 gönderi, bekleme yok)")
    calls_before = client.calls
    before = client.usage_summary()
    stopped = None
    for i in range(burst_calls):
        u = rows[i % len(rows)]["username"]
        try:
            client.business_discovery(config.IG_USER_ID, u, page_size=50, max_media=50)
        except RateLimited as exc:
            stopped = f"rate limit hatası: {exc}"
            break
        except GraphAPIError as exc:
            stopped = f"hata: {exc}"
            break
        u_now = client.usage_summary()
        if (i + 1) % 5 == 0 or i == burst_calls - 1:
            print(f"   {i + 1:>3}. çağrı → {_usage_line(u_now)}")
        worst = max((v for k, v in u_now.items()
                     if k.endswith(("call_count", "total_time", "total_cputime")) and isinstance(v, (int, float))),
                    default=0)
        if worst >= STOP_AT_PCT or u_now.get("buc.regain_min"):
            stopped = f"kullanım %{worst:g} seviyesine ulaştı, güvenlik için durduruldu"
            break
    after = client.usage_summary()
    burst_done = client.calls - calls_before
    if stopped:
        print(f"   DURDU: {stopped}")
    print(f"   Yük testinde {burst_done} çağrı yapıldı.")

    # 3) Yorum — yüzdeler 1 saatlik kayan pencerede birikir; çağrı başına maliyeti
    #    yük testindeki ARTIŞ üzerinden ölçüyoruz ki önceki çalışmalardan kalan yüzdeler karışmasın
    total_calls = client.calls
    print(f"\n3) Sonuç — yük testi {burst_done} çağrı, oturum toplamı {total_calls} çağrı")
    caps: dict[str, float] = {}
    lower_bound_only = True
    for key, label in APP_METRICS:
        pct_after = after.get(key)
        if pct_after is None:
            print(f"   Uygulama/saat — {label}: başlık gelmedi")
            continue
        delta = pct_after - (before.get(key) or 0)
        if burst_done and delta > 0:
            lower_bound_only = False
            caps[key] = burst_done / delta * 100
            print(f"   Uygulama/saat — {label}: {burst_done} çağrı → +%{delta:g} (toplam %{pct_after:g}) "
                  f"→ tavan ≈ {caps[key]:,.0f} çağrı/saat")
        elif pct_after > 0:
            lower_bound_only = False
            caps[key] = total_calls / pct_after * 100
            print(f"   Uygulama/saat — {label}: artış ölçülemedi; oturum toplamından ≈ {caps[key]:,.0f} çağrı/saat")
        else:
            caps[key] = total_calls * 100
            print(f"   Uygulama/saat — {label}: {total_calls} çağrıda hâlâ %0 → tavan en az {caps[key]:,.0f} çağrı/saat")
    if after.get("buc.call_count") is None:
        print("   İş kullanımı (24 saat) başlığı gelmedi → bu token için ayrıca 24 saatlik bir limit görünmüyor.")
    else:
        print(f"   İş kullanımı (24 saat): çağrı %{after['buc.call_count']:g}")

    if not caps:
        print("   Kullanım başlığı hiç gelmedi; kapasite hesaplanamadı.")
        return 0

    binding_key = min(caps, key=caps.get)
    binding_label = dict(APP_METRICS)[binding_key]
    hourly_cap = caps[binding_key]
    safe_hour = hourly_cap * SAFETY
    avg_calls_full = statistics.mean(r["calls"] for r in rows)
    avg_secs = statistics.mean(r["secs"] for r in rows)
    avg_per_day = statistics.mean(r["per_day"] for r in rows)
    approx = "en az" if lower_bound_only else "≈"

    print()
    print(f"   Bağlayıcı limit: {binding_label} → saatte {approx} {hourly_cap:,.0f} çağrı "
          f"(1 saatlik kayan pencere). Güvenli hedef (%{SAFETY * 100:.0f}): {safe_hour:,.0f} çağrı/saat.")
    print(f"   Tek seferde, beklemeden (1 saat içinde) güvenle:")
    print(f"     MEDIA_MAX=50  (hesap başına 1 çağrı)      → {approx} {safe_hour:,.0f} hesap")
    print(f"     MEDIA_MAX={config.MEDIA_MAX} (hesap başına ~{avg_calls_full:.1f} çağrı)   → {approx} {safe_hour / max(avg_calls_full, 1):,.0f} hesap")
    throttled_rate = hourly_cap * config.USAGE_PAUSE_PCT / 100  # otomatik bekleme ile saatlik verim
    print(f"   Daha fazlası için snapshot kullanım %{config.USAGE_PAUSE_PCT:.0f}'e gelince otomatik bekler "
          f"(≈ {throttled_rate:,.0f} çağrı/saat verim). Tahmini çalışma süresi (MEDIA_MAX={config.MEDIA_MAX}):")
    for n in (25, 50, 100, 200):
        calls_needed = n * avg_calls_full
        hours = calls_needed / throttled_rate if calls_needed > safe_hour else 0
        base_min = n * (avg_secs + config.REQUEST_PAUSE) / 60
        print(f"     {n:>4} hesap → {calls_needed:,.0f} çağrı → "
              + (f"~{base_min:.0f} dk" if hours == 0 else f"~{hours:.1f} saat (beklemelerle)"))
    if avg_per_day:
        cover = 50 / avg_per_day
        print(f"   İçerik hızı: bu hesaplar günde ort. {avg_per_day:.1f} paylaşım → 50 gönderi ≈ {cover:.0f} gün, "
              f"100 gönderi ≈ {2 * cover:.0f} gün geriye gider.")
        if cover >= 14 and config.MEDIA_MAX > 50:
            print("   ÖNERİ: MEDIA_MAX=50 yeterli (hesap başına 1 çağrı) → .env içinde MEDIA_MAX=50 yap; kapasite 2 katına çıkar.")
    return 0
