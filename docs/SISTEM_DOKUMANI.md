# IG Snapshot — Sistem Dokümanı

Sürüm: 18.09.2026 · Kod tabanı: `ig_snapshot/` (Python 3.14, Windows) · Bu doküman, projeyi hiç görmemiş bir
geliştiricinin veya yapay zekâ ajanının **kodu okumadan** tüm işleyişi anlaması için yazılmıştır. Kod okunacaksa
buradaki bölüm başlıkları modül adlarıyla eşleşir.

---

## 0. Bir paragrafta sistem

Belirlenen Instagram Business/Creator hesaplarını (bizimkiler + rakipler) **her gece 23:30'da** Meta Graph API
*Business Discovery* ile çeker; her hesabın takipçi sayısını ve son ~45 günlük gönderilerinin (izlenme, beğeni, yorum)
o günkü değerlerini **gün damgalı ölçüm** olarak SQLite'a yazar. Ölçümlerden türetilen raporlar (aylık, genel, kanal
bazlı, günlük-kohort) her gece Excel/Markdown olarak yeniden üretilir; Telegram'a akşam özeti gider; Telegram botu
"dün feed", "bu hafta reels" gibi soruları cevaplar. Hiçbir harici veritabanı/servis yok; her şey PC'de.

---

## 1. Kavramlar ve terimler (önce bunu okuyun)

| Terim | Anlamı |
|---|---|
| **Snapshot / ölçüm** | Bir gönderinin (veya profilin) belirli bir **gündeki** değerleri. `snapshot_date` = çalışmanın başladığı yerel tarih. Aynı gün ikinci çalışma üzerine yazar (tek kayıt/gün). |
| **Reels / Feed** | `media_product_type == "REELS"` → Reels; geri kalan her şey (Fotoğraf, Carousel, eski feed videosu) → **Feed**. YouTube'daki Shorts/uzun video ayrımının karşılığı. |
| **İçerik türü** | `Reels`, `Fotoğraf` (IMAGE), `Carousel` (CAROUSEL_ALBUM), `Video` (VIDEO+FEED), `Diğer`. |
| **Ay paylaşımları** | O ay **yayınlanan** gönderiler ve onların ay içindeki **son ölçülen** değerleri (kohort mantığı). |
| **Kazanılan** | Bir dönemde, takip edilen **tüm** gönderilerin (eski paylaşımlar dahil) ölçülen **artışı** (bugün − dün toplamı). Kanalın o dönemki gerçek performansı. |
| **Kohort** | "O gün yayınlanan içerikler" kesiti; `gunluk-YYYY-MM.xlsx` bu kesittir. |
| **İlk gün değeri** | Gönderinin ilk ölçümü (yayın günü 23:30 ≈ ilk 12–24 saat). |
| **Ay sonu / güncel / sürüklenme** | Ay sonu = ay bitmeden önceki son ölçüm; güncel = en son ölçüm (ay bittikten sonra da artar); sürüklenme = güncel − ay sonu. |
| **Bugünkü içerik / arşiv** | Akşam mesajında: bugün yayınlanan içeriklerden gelen kazanım vs. daha eski içeriklerden gelen kazanım. |
| **Tamamlanma** | Gönderinin günlük artışı sönünce takibi biter, değeri dondurulur. **Raporlarda görünmez**, sadece iç mekanizma. |
| **Budama** | **Kapalı.** Ölçüm silinmez; tamamlanma sadece takibi durdurur. (`PRUNE_AFTER_DAYS>0` ile isteğe bağlı açılabilir.) |
| **Biz / Rakipler** | `accounts.txt` içindeki `[biz]` ve `[rakipler]` bölümleri. Bot cevaplarında ayrı bloklar. |

---

## 2. Mimari

```
Windows Görev Zamanlayıcı
 ├─ "IG Snapshot"  (her gün 23:30, kaçırılırsa açılışta)
 │     └─ scripts\run_snapshot.bat → .venv python -m ig_snapshot snapshot
 │            1. snapshot.run_snapshot()  → Graph API → SQLite (data/ig_snapshot.db)
 │            2. report.generate(ay) + daily_report.generate(ay)   [bu ay + önceki ay]
 │            3. overall.generate()  4. channel_log.generate()
 │            5. notify: akşam özeti → Telegram   6. ay kapanışı (ayda bir)
 └─ "IG Bot"  (oturum açılışında, sürekli, çökerse 1 dk'da yeniden)
       └─ .venv pythonw -m ig_snapshot bot → Telegram long-polling → queries.handle() → cevap

Girdiler:  .env (token, ayarlar) · accounts.txt (hesaplar, gruplar)
Kalıcı veri: data/ig_snapshot.db (SQLite, WAL)
Çıktılar:  reports/YYYY-MM/  reports/genel/  reports/kanallar/  logs/  Telegram
```

Tek dış bağımlılık Meta Graph API (okuma) ve Telegram Bot API'dir. Python paketleri: `requests`, `python-dotenv`,
`openpyxl`.

---

## 3. Dizin yapısı ve modüller

```
IG-snapshot/
  .env / .env.example      ayarlar (aşağıda tam liste)
  accounts.txt             takip listesi  ([biz] / [rakipler])
  requirements.txt
  README.md                (İngilizce, GitHub için; kullanıcı yönetir)
  docs/SISTEM_DOKUMANI.md  bu doküman
  data/ig_snapshot.db      SQLite (git dışı)     data/bot.lock  bot tek-kopya kilidi
  reports/                 üretilen dosyalar (git dışı)
  logs/ig_snapshot.log     uygulama logu (2 MB × 3 döner)   logs/task.log  bat çıktısı
  scripts/
    run_snapshot.bat       görev tarafından çalıştırılır (chcp 65001, PYTHONUTF8=1, venv)
    run_report.bat         elle rapor
    register_task.ps1      "IG Snapshot" görevini kaydeder (-Time HH:MM, -Remove)
    register_bot.ps1       "IG Bot" görevini kaydeder (-Restart, -Remove)
  ig_snapshot/
    config.py        .env okuma, yollar, accounts.txt ayrıştırma, track_cutoff()
    api.py           GraphClient: HTTP, hata/limit yönetimi, business_discovery sayfalama
    db.py            şema, migrasyon, tüm SQL (yazma/okuma/tamamlanma/budama/meta)
    content.py       classify() ve group_of()
    snapshot.py      günlük çekim, istatistik, tamamlanma değerlendirmesi, budama, limit bekleme
    report.py        aylık hesaplama çekirdeği (AccountReport) + rapor-YYYY-MM.* çıktıları
    daily_report.py  kohort raporu gunluk-YYYY-MM.xlsx + highlights_today()
    overall.py       genel görünüm (aylar arası) reports/genel/*
    channel_log.py   hesap başına günlük kayıt reports/kanallar/<hesap>.xlsx
    notify.py        Telegram gönderimi, akşam/ay-sonu/çökme mesajları, token durumu
    bot.py           Telegram long-polling döngüsü
    queries.py       doğal dilli soru ayrıştırma ve cevap üretimi
    limit_test.py    API kapasite ölçümü
    cli.py           komutlar (argparse) ve akış orkestrasyonu
```

---

## 4. Konfigürasyon

### 4.1 `.env`

| Anahtar | Varsayılan | Açıklama |
|---|---|---|
| `IG_ACCESS_TOKEN` | — | Meta Graph API tokenı. Şu an **Page token**; 60 günlük (kalan süre `check` ile görülür) |
| `IG_USER_ID` | otomatik | Sorguyu yapan Instagram profesyonel hesabının ID'si (`check` bulur ve yazar) |
| `FB_APP_ID`, `FB_APP_SECRET` | boş | Sadece `token-refresh` için |
| `GRAPH_API_VERSION` | `v25.0` | |
| `MEDIA_PAGE_SIZE` | `50` | Bir API çağrısında istenen gönderi sayısı (1 sayfa = 1 çağrı) |
| `MEDIA_MAX` | `600` | Hesap başına üst sınır (güvenlik) |
| `TRACK_DAYS` | `45` | Gönderi yayından sonra en fazla bu kadar gün çekilir; sayfalama bu tarihe kadar iner |
| `STOP_RATIO` | `0.2` | Tamamlanma: günlük artış < STOP_RATIO × önceki günün artışı |
| `MIN_TRACK_DAYS` | `3` | Tamamlanma kararı için asgari yaş/ölçüm |
| `PRUNE_AFTER_DAYS` | `0` | 0 = hiçbir ölçüm silinmez (varsayılan, kullanıcı kararı); >0 budamayı açar |
| `REQUEST_PAUSE` | `1.5` | Hesaplar arası bekleme (sn) |
| `USAGE_PAUSE_PCT` | `70` | `X-App-Usage` yüzdesi bunu aşınca bekle |
| `USAGE_SLEEP_SEC` | `600` | Bekleme adımı (sn) |
| `CHANNEL_LOG_DAYS` | `90` | Kanal dosyasındaki gönderi×gün matrisinin genişliği |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | boş | Boşsa bildirim/bot sessizce devre dışı |
| `TOKEN_WARN_DAYS` | `5` | Tokena bu kadar gün kalınca her gece ⚠️ |
| `WEEKLY_FROM` | `2026-09-28` | Haftalık raporun ilk haftası (Pazartesi); öncesi gönderilmez |

`config.py` bunları modül yüklenirken okur (`load_dotenv`), `save_env_value()` ile `.env` güncellenebilir
(`IG_USER_ID`, `TELEGRAM_CHAT_ID`, yeni token). `track_cutoff()` → `(UTC şimdi − TRACK_DAYS)` → `"YYYY-MM-DD"`.

### 4.2 `accounts.txt`

```
# yorum
[biz]              # sonraki satırlar "biz" grubuna girer
hesap_bir          # açıklama
@hesap_iki         # @ olabilir
https://www.instagram.com/hesap_uc/   # link olabilir
[rakipler]         # sonraki satırlar "rakip"
```
`load_account_groups()` → `{handle: "biz"|"rakip"}` (sıra korunur, varsayılan grup `rakip`).
`load_accounts()` → sıra listesi. Hesaplar küçük harfe çevrilir. Listeden çıkarılan hesabın verisi DB'de kalır,
yeni raporlara girmez; eklenen hesap ertesi geceden itibaren çekilir.

---

## 5. Veri modeli — `data/ig_snapshot.db`

SQLite, `journal_mode=WAL` (bot ve snapshot aynı anda okuyabilir). Şema `db.SCHEMA`; ek sütunlar `db._migrate()`
ile `ALTER TABLE` (mevcut DB'ler bozulmaz).

| Tablo | Anahtar | Sütunlar | Notlar |
|---|---|---|---|
| `accounts` | `username` | `ig_id, name, first_seen, last_ok, last_error` | Hata alan hesap `last_error` ile kalır |
| `profile_snapshots` | `(snapshot_date, username)` | `followers_count, follows_count, media_count, fetched_at` | Günde bir satır/hesap |
| `media` | `media_id` | `username, media_type, media_product_type, content_type, caption, permalink, published_at, published_month, first_seen, completed_at` | `published_at` **yerel** ISO (`astimezone()`), `published_month` = `YYYY-MM` yerel. `completed_at` = takibin bittiği gün (NULL = aktif) |
| `media_snapshots` | `(snapshot_date, media_id)` | `like_count, comments_count, view_count, fetched_at` | Tüm hesaplama bu tablodan türer. `view_count` fotoğraf/carousel'de NULL |
| `runs` | `run_id` | `started_at, finished_at, ok_count, fail_count, notes` | Her çalışma |
| `meta` | `key` | `value` | Şu an tek anahtar: `month_end_sent = "YYYY-MM"` |

İndeksler: `media(username, published_month)`, `media_snapshots(media_id, snapshot_date)`,
`profile_snapshots(username, snapshot_date)`.

Boyut tahmini: hesap başına ~9 gönderi/gün, tamamlanmaya kadar ~5–10 ölçüm → 30 hesapta ≈ 2–3 bin satır/gün.
Budama ile tamamlanan gönderiler 3–4 satıra iner.

---

## 6. Veri toplama — `snapshot.py` + `api.py`

### 6.1 API çağrısı (`GraphClient.business_discovery`)

```
GET https://graph.facebook.com/v25.0/{IG_USER_ID}
  ?fields=business_discovery.username({hedef}){
      id,username,name,followers_count,follows_count,media_count,
      media.limit(50)[.after(CURSOR)]{id,media_type,media_product_type,like_count,comments_count,
                                     view_count,caption,timestamp,permalink}}
```
- Sayfalama: yanıtta `media.paging.cursors.after` gelir; iç içe kenarda Meta çoğu zaman `paging.next`
  **vermez**, bu yüzden "tam sayfa geldi ve cursor var" ise devam edilir.
- Durma koşulları (sırayla): sayfa boş / eksik / cursor yok → dur; `len(media) ≥ MEDIA_MAX` → dur (ufka
  inilemediyse uyarı logla); sayfanın **en eski** gönderisi `stop_before` (= `track_cutoff()`) öncesiyse → dur;
  `completed_ids` verildiyse ve sayfada "tamamlanmamış + ufuk içinde" tek gönderi yoksa → dur.
- `view_count` alanı API'ce reddedilirse (kod 100) alan çıkarılıp tekrar denenir.
- Meta'nın `debug_token`, `me/accounts` (User token) veya `me` (Page token) uçları `list_ig_accounts()` içinde;
  Page token'da `me/accounts` yok, `me?fields=instagram_business_account` kullanılır.

### 6.2 Hata ve limit yönetimi (`GraphClient.get`)

- 3 yeniden deneme, 30 sn'den başlayıp ikiye katlanan bekleme: HTTP 5xx, kod 1/2 (geçici), kod
  {4, 17, 32, 613, 80001–80008} veya HTTP 429 (rate limit). Sonunda `RateLimited` / `GraphAPIError`.
- Her yanıtta `X-App-Usage` (1 saatlik kayan pencere: `call_count`, `total_time`, `total_cputime` yüzdeleri) ve
  varsa `X-Business-Use-Case-Usage` başlıkları `last_usage`'a alınır; `usage_summary()` düzleştirir.
- **Ölçülen kapasite (17.09.2026, bu hesaplarla):** bağlayıcı metrik `total_time` → ≈133 çağrı/saat;
  `call_count` ≈240/saat; CPU 0. 45 günlük ufuk bu hesaplarda ≈8 çağrı/hesap. Tamamlanma devreye girince aktif
  bölge daralır (tipik 2–3 sayfa).
- `snapshot.wait_for_capacity()`: her hesaptan sonra `usage_peak()` ≥ `USAGE_PAUSE_PCT` ise `USAGE_SLEEP_SEC`
  bekler (en fazla 6 saat), pencerenin kaydığını görmek için ucuz bir `/me` çağrısı yapar.

### 6.3 Çalışma akışı (`snapshot.run_snapshot(snapshot_date)`)

1. Ön koşullar: token, `IG_USER_ID`, boş olmayan hesap listesi (yoksa `SystemExit` açıklayıcı mesajla).
2. `cutoff = track_cutoff()`; her hesap için sırayla:
   1. `completed = db.completed_ids(username)`
   2. `data = business_discovery(..., stop_before=cutoff, completed_ids=completed)`
   3. `stats[u] = account_stats(...)` — **önce** hesaplanır ki takipçi farkı dünkü ölçüme göre çıksın:
      `followers, followers_delta (vs last_profile_before), posts_today, reels_today, feed_today,
       reels_views_missing, likes_missing`
   4. `store_account(..., skip_ids=completed)`: `accounts` upsert, `profile_snapshots` upsert, her gönderi için
      `media` upsert + `media_snapshots` upsert. **Tamamlanmış gönderiler atlanır** (değer dondurulur).
   5. `evaluate_completion()` (bkz. 6.4)
   6. Hata: `RateLimited` → bu ve kalan hesaplar `failed`, döngü kırılır; `GraphAPIError` → sadece bu hesap
      `failed` + `accounts.last_error`, devam. Aralarda `REQUEST_PAUSE` + `wait_for_capacity`.
3. `runs` kaydı, `prune_old()` (bkz. 6.5).
4. Döner: `{date, ok:[...], failed:{u: hata}, stats:{u:{...}}, waited, elapsed, client}`.

### 6.4 Tamamlanma kuralı (`evaluate_completion`)

Her aktif gönderi (`completed_at IS NULL`, `published_at ≥ cutoff`) için son 3 ölçüm alınır; en sonuncusu bugüne
ait olmalı, gönderi en az `MIN_TRACK_DAYS` günlük olmalı. Metrik: en az bir ölçümde `view_count` varsa izlenme,
yoksa beğeni. `gain_today = v0 − v1`, `gain_prev = v1 − v2`:
- `gain_prev > 0 and gain_today < STOP_RATIO × gain_prev` → tamamlandı, **veya**
- `gain_prev ≤ 0 and gain_today ≤ 0` (iki gündür artış yok) → tamamlandı.
`media.completed_at = bugün`. Sonuç sadece loglanır; raporlara ve Telegram'a yansımaz.

Not: harfiyen "bir gün öncesinden %20 az izlenince" kuralı (`STOP_RATIO=0.8`) Instagram'ın doğal %50+/gün
düşüşü nedeniyle çoğu videoyu 2. gün kapatır; bu yüzden varsayılan 0.2 (yani %80 düşüş) seçildi. `.env`'den
değiştirilebilir.

### 6.5 Budama (`prune_old` → `db.prune_completed`) — VARSAYILAN KAPALI

21.09.2026 kararı: **ölçümler silinmez**; yıl-yıl kıyas için gün gün geçmiş korunur. `PRUNE_AFTER_DAYS>0` verilirse
`snapshot_date − PRUNE_AFTER_DAYS` tarihinden eski ve **tamamlanmış** gönderilere ait ara ölçümler silinir.
Korunan satırlar: gönderinin ilk ölçümü, her takvim ayındaki son ölçümü (ay sonu değeri), en son ölçümü
(tamamlanma değeri). Bu üçlü sayesinde aylık "kazanılan" toplamları, ay sonu/güncel değerleri ve genel rapor
**değişmez**; kaybolan tek şey 90 günden eski günlerin gün-gün dağılımı. ≥5000 satır silinirse `VACUUM`.

---

## 7. Hesaplama çekirdeği — `report.build_account_report(conn, username, month)`

Tüm aylık/genel/kanal/bot hesaplamaları bu fonksiyondan (veya aynı ilkelerden) türer. Çıktı `AccountReport`.

**Girdi verisi:** o ayın `profile_series`; `baseline_profile = last_profile_before(ay başı)`;
hesabın tüm `media` satırları; ay içindeki `media_snapshots`; `baseline = media_baseline_before(ay başı)`
(her gönderinin ay başından önceki son ölçümü); `tracked_since = first_profile_date(hesap)`.

**Takipçi:** `followers_start` = baseline profil (yoksa ayın ilk ölçümü), `followers_end` = ayın son ölçümü.

**Günlük kazanım (Gain: views, likes, comments, views_seen):** ay içindeki ölçümler gönderi×tarih sırasıyla
gezilir; her gönderi için "önceki değer" şöyle tohumlanır:
1. Ay başından önce ölçüm varsa → o değer (ay sınırında süreklilik).
2. Yoksa ve gönderi **bu ay ve `tracked_since` sonrasında** yayınlandıysa → `(0,0,0)` (tüm değeri kazanım; takip
   başladığından beri görülüyor).
3. Aksi hâlde (takipten önce yayınlanmış, ilk kez görülen) → mevcut değer (artış sayılmaz — ilk gün şişmesini
   önleyen kural).
Her ölçümde `max(0, cur − prev)` ilgili günün **Reels/Feed** kovasına eklenir; `views_seen` o kovada hiç izlenme
ölçülüp ölçülmediğini tutar (`views_or_none` → Feed'de "–" gösterimi).

**Ay paylaşımları:** `published_month == ay` olan gönderiler; her biri için ay içindeki **son** ölçüm alınır;
`TypeStats` (count, views/likes/comments toplam + "bilinen" sayaçları → ortalamalar) tür ve grup bazında.

**Günlük satırlar (`DailyRow`):** gün kümesi = profil günleri ∪ paylaşım günleri ∪ kazanım günleri;
`followers_delta` zincirleme (ilk gün baseline'a göre), `posts` (türe göre o gün yayınlananlar), `gain`,
`gain_by_group`.

**Diğer:** `top_posts` (izlenme, sonra beğeni), `prev` (önceki ayın özeti, `with_prev=True` iken),
`view_count_available` (hesap için hiç izlenme gelmediyse rapora uyarı).

`ordered_usernames()` = accounts.txt sırası + o dönemde verisi olan diğer hesaplar.

---

## 8. Raporlar

Hepsi veritabanından **baştan** üretilir (dosyalar elle düzenlenirse ertesi gece ezilir; Excel'de açıkken
yazılamazsa o gece atlanır ve loglanır).

### 8.1 Aylık — `reports/YYYY-MM/` (`report.generate`)
- `rapor-YYYY-MM.md`: özet tablosu (tüm hesaplar; takipçi Δ, paylaşım R/F, kazanılan izlenme/beğeni R/F,
  ort. beğeni), hesap bölümleri (takipçi, paylaşım, ay paylaşımlarının izlenmesi, kazanılan, önceki ay,
  Reels/Feed tablosu, tür tablosu, en çok izlenen 5).
- `rapor-YYYY-MM.xlsx`: `Özet` · `Reels-Feed` · `Türler` · `Günlük` · `İçerikler`.
- `icerikler-YYYY-MM.csv` (`;` ayraçlı, UTF-8 BOM).

### 8.2 Günlük kohort — `reports/YYYY-MM/gunluk-YYYY-MM.xlsx` (`daily_report.generate`)
`PostPerf` = gönderi başına: ilk ölçüm (`first_*`), ay sonuna kadarki son ölçüm (`end_*`), en son ölçüm
(`last_*`), bir önceki ölçüm (`prev_*` → `gain24_*`), `drift_views = last − end`. Tek ölçümü olan gönderide
`prev = 0` (24 saatlik artış = tamamı).
Sayfalar: `Günlük içerik` (gün × hesap; ayın her günü 0'larla), `Reels günlük`, `Feed günlük`,
`Pivot paylaşım/izlenme/beğeni` (gün × hesap, çizgi grafik), `Gönderiler` (ilk gün / ay sonu / güncel /
sürüklenme / 24s Δ), `Öne çıkanlar` (3 liste × 20: güncel izlenme, son 24s artış, beğeni).
`highlights_today(conn, gün)` → `{"reels": top5 izlenme, "feed": top5 beğeni}` (Telegram için).

### 8.3 Genel — `reports/genel/` (`overall.generate`)
`AccountHistory` (hesap × aylar). `genel-rapor.md`: takip başından bugüne tablo + hesap başına ay satırları ve
Toplam. `genel-rapor.xlsx`: `Aylık`, `Takipçi artışı` (ilk ölçüme göre kümülatif, çizgi grafik), `Takipçi`,
`Aylık izlenme` ve `Aylık beğeni` (sütun grafik), `Reels-Feed aylık`, `Günlük (tümü)`.

### 8.4 Kanal dosyaları — `reports/kanallar/<hesap>.xlsx` (`channel_log.generate`)
`Günlük` (tüm zamanlar: takipçi, Δ, profil toplam gönderi ve Δ, o gün atılan içerik türleri, kazanılan
izlenme/beğeni R/F, yorum, izlenen gönderi sayısı; takipçi grafiği), `İzlenme (gün)` ve `Beğeni (gün)`
(gönderi × gün matrisi, son `CHANNEL_LOG_DAYS`), `Gönderiler` (son değerler).

### 8.5 Üretim zamanı
Her snapshot sonunda: **bu ay + önceki ay** (rapor + gunluk) → genel → kanallar. Önceki ayın yenilenme sebebi:
gönderiler ay bittikten sonra da ölçüldüğü için `gunluk` dosyasındaki güncel/sürüklenme sütunları birikir; ay içi
metrikler değişmez. Elle: `report [--month] [--all] [--overall]`.

---

## 9. Telegram

### 9.1 Gönderim (`notify.py`)
`send(text)`: HTML parse mode, 4000 karakterde satır sınırından böler, hata olursa loglar (snapshot'ı durdurmaz).
Sadece `.env`'deki `TELEGRAM_CHAT_ID`'ye gider. `telegram-test` komutu chat id'yi `getUpdates` ile bulur.

### 9.2 Akşam özeti (`build_snapshot_message`)
Sıra: başlık (tarih/saat, N/M hesap, izlenen gönderi, süre, varsa limit beklemesi) → hesap başına 3 satır
(`takipçi (+Δ) · bugün X içerik (R/F)` / `izlenme: bugünkü içerik · arşiv · toplam` /
`beğeni: bugünkü · arşiv · toplam (R/F) · yorum`) → hatalı hesaplar → `🎬 Günün top 5 Reels` →
`🖼 Günün top 5 Feed` → `Veri kontrolü` (gönderi gelmedi, takipçi boş, Reels izlenmesi boş, beğeni gizli,
günde >%5 takipçi değişimi) → token (⚠️ ≤ `TOKEN_WARN_DAYS`, 🚨 geçersiz, yoksa kalan gün) → 📁 satırı.
`cli.today_gains()` bugünkü içerik/arşiv ayrımını üretir: toplam = günün `DailyRow.gain`; bugünkü içerik =
bugün yayınlananların kohort toplamı; arşiv = fark.

### 9.3 Ay kapanışı (`build_month_end_message`, `cli.send_month_end`)
Yeni ayın ilk 10 gününde ilk başarılı snapshot'ta **bir kez** (`meta.month_end_sent`): hesap özetleri
(takipçi Δ %, içerik R/F, kazanılan izlenme/beğeni), ayın en çok izlenen 5 gönderisi, Feed'de en çok beğenilen
3, hesap bazında en iyi. Elle: `month-summary [--month] [--send]`.

### 9.4 Haftalık rapor (`weekly.py`)
Pazartesi→Pazar dönemi. Pazar gecesi 23:30 çekiminden sonra (Pazar çekimi kaçarsa Pzt/Salı telafi) iki mesaj:
(1) hesap blokları — takipçi Δ, içerik R/F, kazanılan izlenme (haftanın içerikleri / arşiv), beğeni R/F, yorum,
Reels ort. izlenme / Feed ort. beğeni, her satırda `↔` önceki haftayla kıyas (yüzde); (2) haftanın Reels top 5 /
en kötü 5, Feed top 5 / en kötü 5. `meta.weekly_sent = "YYYY-Www"` tekrarını engeller; `WEEKLY_FROM` (Pazartesi)
öncesi haftalar gönderilmez. Elle: `week-summary [--end YYYY-MM-DD] [--send]`.

### 9.5 Çökme (`build_crash_message`)
`run_snapshot` beklenmeyen hata verirse 🚨 mesajı gönderilir, hata yükseltilir (görev "başarısız" görünür).

### 9.6 Bot (`bot.py`, `queries.py`)
- `run_bot()`: `getUpdates(timeout=30)` döngüsü; `offset` ile ilerler; yalnız izinli chat; her mesaj →
  `queries.handle()` → `send`. Token bilgisi 12 saatte bir yenilenir. `data/bot.lock` üzerinde `msvcrt.locking`
  ile **tek kopya** garantisi (ikinci kopya "zaten çalışıyor" diyerek çıkar).
- `queries.parse(text)` → `Query(start, end, label, kind, accounts)`:
  dönem: `bugün`, `dün`, `bu hafta` (Pzt→bugün), `geçen hafta`, `son N gün`, `bu ay`, `geçen ay`, `<ay adı> [yıl]`,
  `YYYY-MM`, `DD.MM[.YYYY]`, `bu yıl`, `geçen yıl`; varsayılan **dün**. Tür: `reels/video` → Reels,
  `feed/foto/carousel` → Feed, yoksa `all`. Hesap: `@handle` veya bilinen handle. Türkçe karakterler normalize.
- `answer()`:
  - `all` → Biz / Rakipler blokları: `period_summary` (takipçi Δ ve %, içerik R/F, kazanılan izlenme, beğeni R/F,
    yorum) + Top 5 Reels (izlenme) + Top 5 Feed (beğeni).
  - `Reels`/`Feed` → Biz / Rakipler: hesap başına özet satırı; **tek gün** ise gönderiler listelenir (hesap başına
    ≤12), çok günlü dönemde en iyi 3; sonda **🏆 Genel top 5 (biz dahil)**.
  - `durum` → son çalışma, hesaplar, token günü. Anlaşılmayan metin → yardım.
- `period_summary()` kazanımları ilgili ayların `AccountReport.daily` satırlarından toplar; takipçi başlangıcı
  dönem öncesi son ölçüm.

---

## 10. Komutlar (`python -m ig_snapshot …`)

| Komut | İş |
|---|---|
| `check` | Token geçerlilik/süre/izin, `IG_USER_ID` keşfi ve kaydı, listedeki ilk hesapla deneme sorgusu |
| `snapshot [--date YYYY-MM-DD] [--no-report]` | Gece akışının tamamı |
| `report [--month YYYY-MM] [--all] [--overall]` | Raporları yeniden üret (+ genel + kanallar) |
| `status` | Son çalışma, hesap başına son ölçüm/takipçi/hata |
| `limit-test [hesaplar] [--calls N]` | Hesap başına çağrı maliyeti + yük testiyle saatlik tavan tahmini |
| `telegram-test` | Bot doğrulama, chat id keşfi, test mesajı |
| `week-summary [--end] [--send]` | Haftalık rapor (ekrana / Telegram'a) |
| `month-summary [--month] [--send]` | Ay kapanış mesajı (ekrana / Telegram'a) |
| `bot` | Sohbet botu (sürekli) |
| `ask <metin>` | Bot cevabını Telegram'sız dene |
| `token-refresh` | Uzun ömürlü tokenı yenile (`FB_APP_ID/SECRET` gerekir) |

Konsol UTF-8'e zorlanır (`_utf8_console`), `pythonw` altında konsol handler eklenmez.

---

## 11. Operasyon

- **Görevler:** `IG Snapshot` (günlük 23:30, `StartWhenAvailable`, 2 saat sınır, çakışırsa yenisini yoksay) ve
  `IG Bot` (oturum açılışında, süresiz, `RestartCount 999 / 1 dk`). PC açık ve oturum açık olmalı (kilitli ekran
  olur). Uyku modunda çalışmaz; kaçan çalışma açılışta telafi edilir (gece yarısından sonra kalırsa kayıt ertesi
  güne yazılır; raporlar boş günü tolere eder).
- **Loglar:** `logs/ig_snapshot.log` (uygulama), `logs/task.log` (bat).
- **Token:** 60 gün; `check` kalan günü gösterir; ≤5 gün her gece ⚠️; dolarsa 🚨 ve snapshot alınamaz.
  Yenileme: Graph API Explorer → token → Access Token Debugger → *Extend* → `.env`.
- **Kod güncellemesinden sonra** botu yeniden başlat: `.\scripts\register_bot.ps1 -Restart`.
- **Diğer görevlerle ilişki:** YouTube snapshot görevleriyle (14:00, ay sonu 23:50) klasör/API/kota paylaşımı yok.

---

## 12. API kısıtları ve bilinen davranışlar

- Yalnızca **Business/Creator** hesaplar sorgulanabilir (kişisel hesap → kod 100/110 hata, listede kalır).
- **Fotoğraf ve carousel için izlenme gelmez** (`view_count` NULL); Reels/video'da gelir. Beğeniyi gizleyen
  hesapta `like_count` boş.
- Story, kaydetme, paylaşım, reach yok. Takipçi artışının hangi içerikten geldiği bilinemez.
- Başka hesabın tek bir gönderisi `GET /{media-id}` ile alınamaz → takip ancak sayfalamayla sürdürülebilir; bu
  yüzden ufuk (TRACK_DAYS) ve tamamlanma mekanizması var.
- İlk gün artefaktı: takip başlamadan önce yayınlanan gönderiler kazanım üretmez (7. bölümdeki 3. kural).
- Takip 17.09.2026'da başladı → Eylül raporu kısmi; ilk tam ay Ekim.

---

## 13. Alınan kararlar ve gerekçeleri (tarih sırası)

| Karar | Gerekçe |
|---|---|
| Çalışma 23:30 | Gün sonu değerleri; snapshot_date = o gün |
| Feed = Reels dışı her şey | Tek bir kaba kıyas ekseni (Shorts/uzun video benzeri) |
| Raporlar DB'den her gece baştan | Tutarlılık; geçmiş dosya bozulmaz, veri tek kaynaktan |
| Önceki ay da her gece yenilenir | Sürüklenme izlenmeleri `gunluk` dosyasına biriksin |
| `TRACK_DAYS=45`, tarih bazlı sayfalama (sabit 200 gönderi yerine) | Hesaplar günde ~9 içerik atıyor; 200 gönderi 3 hafta bile değildi |
| Tamamlanma kuralı `0.2`, min 3 gün | API maliyeti ve ölü veri; harfiyen %20 çoğu videoyu 2. gün kapatırdı |
| Tamamlanma raporlarda görünmez, değer dondurulur | Kullanıcı kararı |
| Budama kapalı (0) | Yıl-yıl kıyas için gün gün geçmiş; 30 hesapta ~100 MB/yıl, sorun değil |
| Takipten önceki gönderiler kazanım üretmez | İlk gün "+38M kazanıldı" şişmesi görüldü |
| Akşam mesajında bugünkü içerik / arşiv ayrımı | "Anlık patlayan içerik" görünür olsun |
| Telegram bot long-polling, tek kopya kilidi | Sunucu/webhook yok; iki kopya Telegram'da çakışıyordu |
| Vercel/panel ertelendi | Veri PC'de kalacak; ihtiyaç netleşince (statik JSON / yerel sunucu) |

---

## 14. Test ve doğrulama yöntemi

- **Uçtan uca sahte API testi:** `GraphClient.business_discovery` monkeypatch edilerek 47 günlük sentetik veri
  ile snapshot → aylık/genel raporlar üretildi; toplamların tutarlılığı (aylık Δ toplamı = genel Δ) kontrol edildi.
- **Tamamlanma birim testi:** 1000→+1000→+100 tamamlanır, +500 devam eder, 2 ölçümlü gönderi bekler.
- **Budama testi:** 16 ölçüm → 3 (ilk, ay sonu, son); Mayıs/Haziran kazanılan toplamları değişmedi; tamamlanmış
  gönderiye yeni ölçüm yazılmıyor.
- **Gerçek doğrulama:** Public sample accounts and local targets were used for field and limit measurements;
  comparison exports remained under ignored `data/kontrol/*.csv`.

---

## 15. Planlanan / tartışılacak: 7 – 14 – 30 günlük raporlar

**İstek (18.09.2026):** aylık raporlara ek olarak 7, 14 ve 30 günlük raporlar. Henüz **kodlanmadı**; yapı önce
bu doküman üzerinde tartışılacak.

Mevcut altyapıda hazır olan parçalar:
- `queries.period_summary(conn, hesap, start, end)` → herhangi bir pencere için takipçi Δ, içerik R/F,
  kazanılan izlenme/beğeni/yorum (aylık `DailyRow`'lardan toplanır).
- `queries.posts_in_range(...)` → pencerede yayınlanan gönderiler (`PostPerf`: ilk gün / güncel / 24s Δ).
- Bot zaten `son 7 gün`, `son 14 gün`, `son 30 gün` sorgularına cevap veriyor (Telegram metni olarak).
- **Haftalık Telegram raporu** (21.09.2026'da eklendi) Pazar geceleri otomatik gidiyor; 7 günlük Excel ihtiyacını kısmen karşılar.

Öneri taslağı (tartışmaya açık):
- `reports/donem/` altında her gece üç dosya: `son7.xlsx`, `son14.xlsx`, `son30.xlsx` (+ `.md`), kayan pencere
  (bugün dahil geriye N gün). Sayfalar: `Özet` (hesaplar yan yana: takipçi Δ, içerik R/F, kazanılan
  izlenme/beğeni R/F, ort. izlenme/Reels, ort. beğeni/Feed), `Önceki pencereyle kıyas` (son 7 vs önceki 7),
  `Günlük` (pencere içi gün × hesap), `Gönderiler` (pencerede yayınlananlar), `Top` (Reels izlenme, Feed beğeni,
  24s artış).
- Alternatif: sabit takvim (ISO hafta / 2 hafta) — karşılaştırma daha "resmi", ama medya ritmi için kayan
  pencere daha kullanışlı.

Netleşmesi gereken sorular:
1. Kayan pencere mi, takvim haftası mı? (İkisi de olabilir.)
2. Kesit: kohort (pencerede yayınlananların topladığı) mı, kazanılan (pencere içi artış) mı, ikisi de mi?
3. Çıktı: Excel dosyaları mı, Telegram (bot komutu + haftalık otomatik mesaj) mı, ikisi mi?
4. Önceki pencereyle kıyas ve yüzde değişim istenir mi?
5. Üretim: her gece otomatik mi, istek üzerine mi? (Her gece maliyetsiz.)

## 16. Veri biriktikten sonra tartışılacak konular (21.09.2026)

Karar: aşağıdakiler birkaç haftalık gerçek ölçüm toplanmadan kurala bağlanmayacak.

1. **Akşam atılan içerikler.** 23:30 çekiminde yayınlanalı 1–3 saat olmuş içerikler günün top / en kötü 5
   listelerine tam günlük içeriklerle aynı kefede giriyor; izlenmeleri ertesi güne sarkıyor. Takip zaten sürdüğü
   için veri kaybı yok, sorun değerlendirme zamanı. Aday çözümler: N saatten genç içerikleri listeden muaf tutmak,
   "ilk 24 saat" normalizasyonu (yayın saatine göre), ertesi gün mesajında "dünkü içerikler bugün" bloğu, saat bazlı
   beklenti eğrisi. Ölçüm kaynağı: `gunluk-*.xlsx` (ilk gün / güncel), kanal dosyalarındaki gönderi×gün matrisi.
2. **Tamamlanma eşiği** (`STOP_RATIO`, `MIN_TRACK_DAYS`): kapanma yaşı dağılımı ve erken kapanan viral video var mı.
3. **7 / 14 / 30 günlük Excel raporları** (§15); haftalık Telegram raporu ara çözüm.
4. Rakip listesi gelince kapasite ve biz-vs-rakipler kıyas görünümleri.

Diğer açık işler: README'ye yeni komutların eklenmesi (kullanıcı yönetiyor), kod değişikliklerinin commit'i,
şefin rakip listesinin `[rakipler]` bölümüne girmesi ve kapasiteye göre `TRACK_DAYS`/`STOP_RATIO` gözden geçirme.
