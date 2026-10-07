# IG Snapshot — Nasıl Çalışıyor (kaba kod + şemalar)

Bu doküman sistemin **ne yaptığını** ve **nasıl yaptığını** şema ve sözde-kod (gerçek koda çok yakın ama
sadeleştirilmiş) ile anlatır. Ayrıntılı teknik referans: `SISTEM_DOKUMANI.md`. Kurulum/komutlar: `README.md`.

---

## 1. Kuş bakışı

```
                    ┌───────────────────────────────────────────────┐
   accounts.txt ───▶│                                               │
   (kim izlenecek)  │            HER GECE 23:30                     │
                    │         (Windows Görev Zamanlayıcı)           │
   .env ───────────▶│                                               │
   (token, ayarlar) │   1. Meta Graph API'den çek                   │
                    │   2. SQLite'a "bugünün ölçümü" olarak yaz     │
                    │   3. Ömrü dolan gönderilerin takibini bitir   │
                    │   4. Raporları baştan üret                    │
                    │   5. Telegram'a özet gönder                   │
                    └───────────┬───────────────────────────────────┘
                                │
              ┌─────────────────┼──────────────────┬─────────────────────┐
              ▼                 ▼                  ▼                     ▼
      data/ig_snapshot.db   reports/*.xlsx   Telegram mesajları    logs/*.log
      (ASIL VERİ, silinmez)  (okunabilir)     (günlük/haftalık)     (denetim)
                              ▲
                              │  aynı veritabanından
                     ┌────────┴────────┐
                     │  IG Bot         │  ◀── "dün feed", "bu hafta reels"
                     │ (sürekli çalışır)│
                     └─────────────────┘
```

**Temel ilke:** API'den gelen her şey `(gün, nesne)` anahtarlı **ölçüm** olarak saklanır. Rapor, mesaj, bot cevabı
— hepsi bu ölçümlerden *sonradan* hesaplanır. Hiçbir rapor "kaynak" değildir; istediğimiz zaman hepsi yeniden üretilir.

---

## 2. Gece akışı — kaba kod

```python
def gece_calismasi(bugun):
    hesaplar = accounts_txt_oku()          # [biz] ve [rakipler] blokları, sırayla
    ufuk     = bugun - 45_gun              # TRACK_DAYS: bundan eski gönderi çekilmez

    for hesap in hesaplar:
        biten = db.takibi_biten_gonderiler(hesap)      # bunlar artık sorgulanmayacak

        # ---- 1) API'den çek (sayfa sayfa) ----------------------------------
        profil, gonderiler = api_cek(hesap, ufuk, biten)

        # ---- 2) Veritabanına yaz -------------------------------------------
        db.yaz(profile_snapshots, gun=bugun, hesap=hesap,
               takipci=profil.followers, takip_edilen=profil.follows,
               toplam_gonderi=profil.media_count)

        for g in gonderiler:
            if g.id in biten:                 # ömrü dolmuş: değeri dondurulmuş
                continue
            db.yaz_veya_guncelle(media, g.id, hesap, tur=sinifla(g),
                                 yayin_zamani=g.timestamp, aciklama=g.caption, link=g.permalink)
            db.yaz(media_snapshots, gun=bugun, gonderi=g.id,
                   izlenme=g.view_count, begeni=g.like_count, yorum=g.comments_count)

        # ---- 3) Ömrü dolanları işaretle ------------------------------------
        for g in db.aktif_gonderiler(hesap, ufuk):
            if omru_doldu(g):                 # bkz. bölüm 5
                db.isaretle(g, completed_at=bugun)

    # ---- 4) Raporlar (her gece sıfırdan) -----------------------------------
    for ay in [bu_ay, onceki_ay]:
        aylik_rapor(ay);  gunluk_kohort_raporu(ay)
    genel_rapor();  kanal_dosyalari()

    # ---- 5) Telegram --------------------------------------------------------
    gonder(mesaj_1_hesap_bloklari(), mesaj_2_top5_listeleri())
    if bugun.pazar:      gonder(haftalik_rapor())       # Pzt→Paz
    if bugun.ayin_ilki:  gonder(ay_kapanisi())          # ayda bir kez
    if token_suresi <= 5_gun: gonder(uyari())
```

### API çekimi — sayfalama ne zaman durur?

```python
def api_cek(hesap, ufuk, biten):
    gonderiler, imlec = [], None
    while True:
        sayfa = graph_api(f"business_discovery.username({hesap})"
                          f"{{profil_alanlari, media.limit(50).after({imlec}){{gonderi_alanlari}}}}")
        gonderiler += sayfa.veri
        if sayfa.bos or sayfa.tam_degil or sayfa.imlec_yok:      break   # başka sayfa yok
        if sayfa.en_eski_gonderi < ufuk:                          break   # 45 günü aştık
        if sayfa.tum_gonderiler_biten_listesinde:                 break   # izlenecek bir şey kalmadı
        if len(gonderiler) >= MEDIA_MAX:                          break   # güvenlik sınırı (600)
        imlec = sayfa.imlec
    return sayfa.profil, gonderiler
```

> Gönderiler yeniden eskiye sıralı gelir. Tamamlanma mekanizması sayesinde aktif bölge küçüldükçe
> hesap başına çağrı 8'den 2–4'e iner.

---

## 3. Veri nerede duruyor?

`data/ig_snapshot.db` (SQLite). **Hiçbir satır silinmez.**

```
   accounts                          profile_snapshots
 ┌──────────────┐                  ┌───────────────────────┐
 │ username  PK │◀────────────────┐│ snapshot_date  ┐      │
 │ ig_id        │  (kimlik; ad    ││ username       ┘ PK   │   HER GÜN 1 SATIR / HESAP
 │ name         │   değişiminde   ││ followers_count       │
 │ first_seen   │   birleştirme)  ││ follows_count         │
 │ last_ok      │                 ││ media_count           │
 │ last_error   │                 ││ fetched_at            │
 └──────────────┘                 │└───────────────────────┘
        ▲                         │
        │ username                │ username
        │                         │
   media│                         │            media_snapshots
 ┌──────┴────────────────┐        │        ┌────────────────────────┐
 │ media_id           PK │◀───────┼────────│ snapshot_date  ┐       │
 │ username              │────────┘        │ media_id       ┘ PK    │  HER GÜN 1 SATIR / GÖNDERİ
 │ media_type            │  VIDEO/IMAGE/…  │ view_count             │  (takibi bitene kadar)
 │ media_product_type    │  REELS/FEED     │ like_count             │
 │ content_type          │  Reels/Fotoğraf/│ comments_count         │
 │ caption, permalink    │  Carousel/Video │ fetched_at             │
 │ published_at          │  yerel saat     └────────────────────────┘
 │ published_month       │  "2026-09"
 │ first_seen            │
 │ completed_at          │  NULL = hâlâ izleniyor
 └───────────────────────┘

   runs                             meta
 ┌────────────────────┐          ┌──────────────────────────────┐
 │ run_id, started_at │          │ key   → "weekly_sent"        │
 │ finished_at        │          │ value → "2026-W40"           │
 │ ok_count, fail_count│         │ (haftalık/aylık mesaj bir kez │
 │ notes              │          │  gitsin diye)                │
 └────────────────────┘          └──────────────────────────────┘
```

### Hangi bilgi hangi tabloda — örnek satırlarla

| Bilgi | Tablo | Örnek |
|---|---|---|
| Hesabın kimliği, son başarı/hata | `accounts` | `hesap_adi · ig_id 178414xxxxxxxxx · Görünen Ad · last_ok 27.09 23:30` |
| **Takipçi sayısı (gün gün)** | `profile_snapshots` | `2026-09-17 · hesap_adi · 536.885 takipçi · 172 takip · 13.329 gönderi` |
| Gönderinin kimliği ve türü | `media` | `1813034xxxxxxxxx · Carousel · FEED · yayın 17.09 01:46 · completed_at NULL` |
| **İzlenme/beğeni/yorum (gün gün)** | `media_snapshots` | `2026-09-17 · 1813034xxxxxxxxx · beğeni 4.264 · yorum 32 · izlenme NULL (fotoğrafta yok)` |
| Çalışma kaydı | `runs` | `#1 · 17.09 12:18→12:19 · 5 OK / 0 hata` |
| Gönderilen periyodik mesaj işareti | `meta` | `weekly_sent = 2026-W40` |

Bugünkü hacim (28.09.2026): 5 hesap · 2.157 gönderi · **10.493 gönderi ölçümü** · 45 profil ölçümü · 2,3 MB.
Yıllık tahmin (30 hesap): ~100 MB.

---

## 4. Bir gönderinin hayatı

```
 Pzt 18:00   Reels yayınlandı
      │
 Pzt 23:30   ölçüm 1 →  120.000 izlenme   ← "bugünkü içerik" sayılır, günün top5'ine girer
      │                      artış: +120.000
 Sal 23:30   ölçüm 2 →  400.000           artış: +280.000   ← o günün "kazanılan"ına yazılır
 Çar 23:30   ölçüm 3 →  520.000           artış: +120.000
 Per 23:30   ölçüm 4 →  560.000           artış:  +40.000
 Cum 23:30   ölçüm 5 →  572.000           artış:  +12.000   ← 12.000 < 0,20 × 40.000 (=8.000)? HAYIR
 Cmt 23:30   ölçüm 6 →  577.000           artış:   +5.000   ← 5.000 < 0,20 × 12.000 (=2.400)? HAYIR
 Paz 23:30   ölçüm 7 →  578.000           artış:   +1.000   ← 1.000 < 0,20 × 5.000 (=1.000)? EVET
      │                                      ⇒ completed_at = Pazar   TAKİP BİTTİ
      ▼
 Bundan sonra: API'ye sorulmaz, yeni satır yazılmaz, değeri 578.000 olarak sabit kalır.
 Bu 578.000, gönderinin yayınlandığı AYIN raporunda 'nihai izlenme' olarak yer alır — ömrü sonraki aya
 taşmış olsa bile. Ay raporu, o ayın hâlâ izlenen gönderisi kalmayana kadar her gece yenilenir; sonra sabitlenir.
 Tüm 7 ölçüm veritabanında durur (silinmez) → gün gün geçmiş korunur.
```

Kural (`STOP_RATIO=0.2`, `MIN_TRACK_DAYS=3`):

```python
def omru_doldu(gonderi):
    s = son_3_olcum(gonderi)
    if len(s) < 3 or gonderi.yas < 3_gun:        return False
    metrik = "izlenme" if gonderi.reels else "begeni"     # Feed'de izlenme gelmiyor
    bugunku_artis = s[0][metrik] - s[1][metrik]
    dunku_artis   = s[1][metrik] - s[2][metrik]
    return (dunku_artis > 0 and bugunku_artis < 0.20 * dunku_artis) \
        or (dunku_artis <= 0 and bugunku_artis <= 0)      # iki gündür hiç artış yok
```

---

## 5. İki farklı sayım — en çok karıştırılan yer

```
   GÜN:        17 Eyl        18 Eyl        19 Eyl
              ┌──────┐      ┌──────┐      ┌──────┐
 17'de atılan │ +120K│─────▶│ +280K│─────▶│ +120K│   (aynı gönderi büyümeye devam eder)
 16'da atılan │  +90K│─────▶│  +30K│─────▶│  +10K│
 10'da atılan │   +5K│─────▶│   +4K│─────▶│   +3K│
              └──────┘      └──────┘      └──────┘

 KOHORT  (17 Eyl satırı)  = 120K + 280K + 120K + …  ▶ "17'sinde atılan içerik bugüne kadar ne yaptı"
 KAZANILAN (18 Eyl sütunu)= 280K +  30K +   4K      ▶ "18'inde kanal toplamda ne kazandı"
```

| Nerede görünür | Hangisi |
|---|---|
| Telegram ▶️ satırı ("izlenme: bugünkü içerik X · arşiv Y") | **Kazanılan** (X = bugünkü kohort, Y = eski içeriklerden gelen) |
| Telegram günün top 5 / en kötü 5 | **Kohort** (bugün yayınlananlar) |
| `gunluk-YYYY-MM.xlsx` | **Kohort** (gün × hesap; ilk gün / ay sonu / güncel) |
| Aylık rapordaki "kazanılan izlenme/beğeni" | **Kazanılan** |
| Aylık rapordaki "ay paylaşımları" | **Kohort — nihai değerle** (gönderi ömrü sonraki aya taşsa da ulaştığı son değer) |
| Haftalık rapor ▶️ / ❤️ | **Kazanılan** (+ parantezde haftanın içerikleri = kohort) |

Kazanılan hesabı (kaba kod):

```python
def kazanilan(hesap, baslangic, bitis):
    toplam = {"Reels": 0, "Feed": 0}
    for gonderi in hesabin_gonderileri:
        onceki = gonderinin_donem_oncesi_son_olcumu(gonderi)      # ay/hafta sınırında süreklilik
        if onceki is None:
            onceki = 0 if gonderi_takip_basladiktan_sonra_yayinlandi else ilk_olcum
        for olcum in gonderinin_donemdeki_olcumleri:
            artis = max(0, olcum.deger - onceki.deger)            # negatif artış sayılmaz
            toplam[gonderi.grup] += artis
            onceki = olcum
    return toplam
```

> Üçüncü satırdaki kural önemli: takip başlamadan **önce** yayınlanmış bir gönderinin birikmiş değeri
> "bugün kazanıldı" sayılmaz; ilk ölçümü başlangıç kabul edilir.

---

## 6. Raporlar — ne, nereden, nasıl

```
            data/ig_snapshot.db
                    │
     ┌──────────────┼───────────────┬────────────────────┐
     ▼              ▼               ▼                    ▼
 report.py     daily_report.py   overall.py        channel_log.py
 (aylık)        (günlük kohort)  (aylar arası)      (hesap başına)
     │              │               │                    │
     ▼              ▼               ▼                    ▼
 reports/2026-09/   reports/2026-09/  reports/genel/   reports/kanallar/
  rapor-2026-09.md   gunluk-2026-09    genel-rapor.md    hesap_bir.xlsx
  rapor-2026-09.xlsx  .xlsx            genel-rapor.xlsx  hesap_iki.xlsx …
  icerikler-*.csv
```

| Dosya | Satır = | İçerik |
|---|---|---|
| `rapor-YYYY-MM.xlsx` | hesap / tür / gün | Özet · Reels-Feed · Türler · Günlük · İçerikler |
| `gunluk-YYYY-MM.xlsx` | **gün × hesap** | O gün atılan içerik sayısı ve topladığı izlenme/beğeni; ilk gün / ay sonu / güncel / son 24s; pivotlar + grafik; öne çıkanlar |
| `genel-rapor.xlsx` | **ay × hesap** | Takipçi artışı (grafik), aylık izlenme/beğeni (grafik), Reels-Feed aylık |
| `kanallar/<hesap>.xlsx` | **gün** ve **gönderi × gün** | Günlük takipçi/kazanım; izlenme ve beğeni matrisi (90 gün) |

Her gece **bu ay + önceki ay** yeniden üretilir (önceki ay: gönderiler ay bittikten sonra da büyüdüğü için
"güncel" ve "sürüklenme" sütunları birikir; ay içi metrikler değişmez).

---

## 7. Telegram

```
 GECE 23:30 sonrası                         PAZAR geceleri              AY BAŞI (1–10)
 ┌────────────────────┐                  ┌────────────────────┐      ┌──────────────────┐
 │ MESAJ 1            │                  │ Haftalık MESAJ 1   │      │ Ay kapanışı      │
 │ ▬ @hesap           │                  │ (Pzt→Paz, ↔ önceki │      │ • hesap özetleri │
 │ 👥 takipçi (+Δ)    │                  │  haftayla kıyas)   │      │ • ayın top 5'i   │
 │ 📝 içerik R/F      │                  ├────────────────────┤      │ • Feed top 3     │
 │ ▶️ izlenme (bugün/ │                  │ Haftalık MESAJ 2   │      └──────────────────┘
 │    arşiv)          │                  │ top5 / en kötü 5   │        (meta ile 1 kez)
 │ ❤️ beğeni (R/F)    │                  └────────────────────┘
 │ 💬 yorum           │
 │ ❌ alınamayan hesap│                   UYARILAR: token ≤5 gün, token geçersiz,
 │ ⚠️ veri kontrolü   │                   çalışma çöktü, veri anormallikleri
 │ 🔑 token           │
 ├────────────────────┤
 │ MESAJ 2            │
 │ 🎬 Reels top 5     │
 │ 🎬 en kötü 5       │
 │ 🖼 Feed top 5      │
 │ 🖼 en kötü 5       │
 └────────────────────┘
```

### Bot (sürekli çalışır, PC açıkken)

```python
while True:
    soru = telegram_bekle()                    # uzun yoklama, sadece izinli sohbet
    donem, tur, hesaplar = ayristir(soru)      # "dün feed @hesap" → (dün, Feed, [hesap])
    cevap = ayni_hesaplamalar(donem, tur, hesaplar)   # gece mesajıyla aynı yapı
    gonder(cevap)                              # 1–2 mesaj
```

Tanıdığı dönemler: `bugün · dün · bu hafta · geçen hafta · son N gün · bu ay · geçen ay · <ay adı> · YYYY-MM ·
GG.AA · bu yıl · geçen yıl`. Türler: `reels`, `feed`. Ek: `durum`.

---

## 8. Hata ve limit yönetimi

```
 API çağrısı
     │
     ├─ 200 OK ─────────────────────────▶ devam
     │
     ├─ rate limit (4/17/32/613/8000x) ─▶ 30 sn bekle → 60 → 120 (3 deneme)
     │                                     hâlâ olmuyorsa: kalan hesaplar ertesi güne
     │
     ├─ kod 110 "Invalid user id" ──────▶ "hesap bulunamadı" (ad değişti / banlandı / kişisel)
     │                                     o hesap atlanır, diğerleri devam eder
     │
     └─ kod 190 ────────────────────────▶ "token geçersiz" → 🚨 Telegram

 Her yanıttan sonra: X-App-Usage yüzdesi okunur
     kullanım ≥ %70  →  10 dk bekle, tekrar dene (en fazla 6 saat)
```

Ad değişiminde ne olur:

```python
if profil.ig_id başka bir kullanıcı adıyla kayıtlıysa:
    tüm geçmişi yeni ada taşı            # (18.09.2026'da bir hesapta yaşandı)
    log: "@eski → @yeni, geçmiş birleştirildi"
```
Yeni adı `accounts.txt`'ye **senin** yazman gerekir (API eski adı bulamaz); akşam mesajındaki ❌ satırı bunu haber verir.

---

## 8a. CSV defteri (ham kaydın değişmez kopyası + yedek)

Her gece, veritabanına yazdıktan sonra aynı veri düz CSV olarak da yazılır:

```
 data/
   gonderiler.csv              gönderi künyesi (tür, yayın, açıklama, link) — yedeğin kaynağı
   gunluk/2026-11-07.csv       SADECE 7 Kasım: her gönderinin o günkü değeri + o günkü artışı + durum
   gunluk/ozet/2026-11-07.csv  o günün hesap özeti (takipçi, paylaşım, kazanım)
   haftalik/2026-W45.csv       Pzt→Paz kümülatif · her gece güncellenir · Pazartesi YENİ dosya
   haftalik/gonderi/2026-W45.csv
   aylik/2026-11.csv           ay başından bugüne kümülatif · ayın 1'inde YENİ dosya
   aylik/gonderi/2026-11.csv
```

Kurallar:
- **Günlük dosya bir kez yazılır**, bir daha dokunulmaz → denetim izi.
- Ömrü dolan gönderi, son ölçüldüğü günün dosyasında `ömrü doldu` damgasıyla görünür; sonraki günlerde yer almaz.
- **Haftalık/aylık dosyalar sıfırlanmaz, yenilenir**: yeni dönem = yeni dosya. Biten dönem, içindeki
  gönderiler ömrünü tamamlayana kadar güncellenmeye devam eder, sonra kendiliğinden sabitlenir.
- Hafta ay sınırını aşabilir (Pzt→Paz bütündür); haftalık ve aylık birbirinden bağımsızdır.
- İki sütun ayrımı: `donem_icerikleri_*` = o dönemde yayınlananların ulaştığı değer (içerik performansı),
  `kazanilan_*` = eskiler dahil tüm arşivin o dönemdeki artışı (kanal hareketi).

**Yedek tatbikatı:** `python -m ig_snapshot rebuild --compare` bu CSV'lerden veritabanını sıfırdan kurar
ve mevcutla karşılaştırır. 07.10.2026 testinde 14.434 ölçüm, 2.357 künye ve 77 profil satırı **birebir**
geri yüklendi.

## 8b. Veriyi dışa aktarma (inceleme / arşiv)

```
 python -m ig_snapshot export --month 2026-09 --db
             │
             ▼
 reports/disa-aktarim/2026-09/
   ├─ profil-olcumleri.csv      hesap × gün: takipçi, takip edilen, toplam gönderi
   ├─ gonderiler.csv            o ay yayınlananlar + nihai izlenme/beğeni/yorum
   ├─ gonderi-olcumleri.csv     GÜN GÜN ham ölçümler (asıl veri)
   ├─ veri-2026-09.xlsx         aynı üç tablo tek Excel'de
   ├─ ig_snapshot-YYYYMMDD.db   veritabanının dondurulmuş kopyası (arşiv)
   └─ OKUBENI.txt               sütun açıklamaları
```

`--all` tüm veriyi, `--month` tek ayı aktarır. **Ay bitince** (yeni ayın ilk çalışmasında) biten ay
otomatik olarak, veritabanı kopyasıyla birlikte, bir kez aktarılır (`meta.month_export_done`).

## 9. Ayar düğmeleri (`.env`)

| Ayar | Şu an | Ne yapar |
|---|---|---|
| `TRACK_DAYS` | 45 | Gönderi en fazla bu kadar gün çekilir |
| `STOP_RATIO` / `MIN_TRACK_DAYS` | 0.2 / 3 | Ömür kuralı (bölüm 4) |
| `PRUNE_AFTER_DAYS` | **0** | 0 = hiçbir ölçüm silinmez (yıl-yıl kıyas için) |
| `MEDIA_PAGE_SIZE` / `MEDIA_MAX` | 50 / 600 | Sayfa boyu / güvenlik sınırı |
| `USAGE_PAUSE_PCT` / `USAGE_SLEEP_SEC` | 70 / 600 | Limit freni |
| `CHANNEL_LOG_DAYS` | 90 | Kanal dosyasındaki matris genişliği |
| `WEEKLY_FROM` | 2026-09-28 | Haftalık raporun ilk haftası |
| `TOKEN_WARN_DAYS` | 5 | Token uyarısı eşiği |

---

## 10. Şu anki durum (28.09.2026)

```
 Takip başlangıcı : 17.09.2026
 Aktif hesaplar   : 4 kendi kanalımız                                            [biz]
 Pasif            : 1 hesap (banlandı 21.09 — listede kapalı, geçmiş veri duruyor)
 Bekleyen         : rakip listesi ([rakipler] bölümüne)
 Veri             : 45 profil ölçümü · 2.157 gönderi · 10.493 gönderi ölçümü · 2,3 MB
 Otomasyon        : "IG Snapshot" 23:30 · "IG Bot" sürekli · haftalık ilk rapor 04.10 · ay kapanışı 01.10
```
