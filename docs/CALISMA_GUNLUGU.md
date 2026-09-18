# IG Snapshot — Çalışma Günlüğü (16–18 Eylül 2026)

Bu dosya, sistemin **nasıl kurulduğunu adım adım** anlatır (ne yapıldı, neden, ne test edildi). Sistemin nasıl
çalıştığını anlatan teknik doküman ayrı: `docs/SISTEM_DOKUMANI.md`.

---

## 16 Eylül — Fizibilite ve ilk kurulum

1. **Soru:** YouTube Data API ile yaptığımız günlük rakip takibinin Instagram karşılığı mümkün mü?
   **Cevap:** Evet, resmi yol Instagram Graph API *Business Discovery*. Farklar: API key yerine OAuth token,
   kendi Business hesabı + Facebook Page gerekir, sadece Business/Creator hesaplar sorgulanabilir, story yok,
   fotoğraf/carousel izlenmesi yok. Meta dokümanından `view_count`'un artık rakip medyada da döndüğü doğrulandı.
2. **Proje iskeleti kuruldu** (`ig_snapshot/` paketi, venv, `requirements.txt`, `.env.example`, `accounts.txt`):
   - `api.py` Graph API istemcisi (sayfalama, yeniden deneme), `db.py` SQLite şeması, `snapshot.py` günlük çekim,
     `report.py` aylık rapor (MD + Excel + CSV), `cli.py` komutlar (`check / snapshot / report / status / token-refresh`).
   - `scripts/run_snapshot.bat`, `run_report.bat`, `register_task.ps1` (Görev Zamanlayıcı, 23:30).
3. **Sahte API ile uçtan uca test:** 47 günlük sentetik veri → Ağustos + Eylül raporları; eksik günler ve
   profesyonel olmayan hesap hatası senaryoları geçti.
4. **Reels/Feed kırılımı eklendi:** beğeni ve izlenme Reels ve Feed için ayrı; aylar arası **genel görünüm**
   (`reports/genel/`, takipçi artışı ve aylık izlenme/beğeni grafikleriyle Excel).

## 17 Eylül — Canlıya alma

5. **Token:** A local development token was configured in `.env` (never commit this file).
   `check` validated it. The token was a **Page token**; `me/accounts` yerine `me` ile hesap keşfi
   eklendi → IG_USER_ID otomatik yazıldı. A public sample account was used to validate Business Discovery.
6. **Limit testi** (`limit-test` komutu eklendi): `X-App-Usage` başlığıyla ölçüm. Bağlayıcı limit *toplam süre*
   ≈133 çağrı/saat; çağrı sayısı ≈240/saat. Snapshot'a **otomatik yavaşlama** eklendi (kullanım %70'te bekle).
   İç içe sayfalamada Meta `next` vermediği için cursor tabanlı sayfalama düzeltildi.
7. **Initial targets:** A small local set of Business/Creator accounts was configured. **İlk gerçek snapshot**
   completed successfully; the scheduled task and full report chain were verified.
8. **Data validation:** A recent media sample was exported locally under `data/kontrol/` for comparison.
   This directory is ignored and must never be published. Missing photo views are shown as unavailable in reports.
9. **Kanal bazlı günlük Excel** (`reports/kanallar/<hesap>.xlsx`): günlük takipçi/toplam gönderi/kazanım,
   gönderi×gün izlenme ve beğeni matrisleri. `MEDIA_MAX` 200'e çıkarıldı (hesaplar günde ~9 içerik atıyor).
10. **Telegram bildirimi:** bot oluşturuldu (`telegram-test` chat id'yi buldu). Her gece özet, token ≤5 gün
    uyarısı, çökme alarmı. İlk otomatik özet 23:32'de geldi.
11. **Günlük içerik kohort raporu** (`gunluk-YYYY-MM.xlsx`): o gün atılan içeriklerin topladığı izlenme/beğeni,
    ilk gün / güncel, son 24 saat artışı, öne çıkanlar; Telegram'a "bugünün öne çıkanları" eklendi.
12. **Ölçüm süresi tarih bazlı oldu:** sabit 200 gönderi yerine `TRACK_DAYS=45` — sayfalama 45 günden eskiye
    kadar iner (8 çağrı/hesap ölçüldü). Ay kapanışı Telegram mesajı (top 5, hesap özetleri) ve `month-summary`
    komutu eklendi; önceki ay raporları da her gece yenilenir (sürüklenme izlenmeleri birikir).
13. **İlk gün artefaktı düzeltildi:** takip başlamadan önce yayınlanmış gönderilerin tüm izlenmesi "bugün
    kazanıldı" sayılıyordu (+38M gibi); artık sadece takipten sonra yayınlananlar tam kazanım üretir.
14. Akşam mesajı yeniden düzenlendi: "takipçi" etiketi, **bugün kazanılan izlenme/beğeni**, **günün top 5 Reels
    (izlenme) + top 5 Feed (beğeni)**.

## 18 Eylül — Bot, tamamlanma, budama, dokümantasyon

15. **Akşam mesajı:** izlenme "bugünkü içerik / arşiv" olarak ayrıldı.
16. **Telegram sohbet botu** (`bot.py`, `queries.py`, `register_bot.ps1`): "dün ne oldu", "dün feed",
    "bu hafta reels", "geçen ay", "eylül", "son 3 gün", "@hesap" süzgeci, "durum". Cevaplar Biz / Rakipler /
    Genel top 5 bloklu. `accounts.txt`'ye `[biz]` / `[rakipler]` grupları eklendi. Bot oturum açılışında başlar,
    çökerse yeniden başlar; iki kopya çakışması görülünce tek-kopya kilidi eklendi.
17. **Takip tamamlanma kuralı:** günlük artış önceki günün %20'sinin altına inince (≥3 gün) gönderi tamamlanır
    (`STOP_RATIO=0.2`, harfiyen %20 için 0.8; uyarı yapıldı). Tamamlanan gönderiler sayfalamayı kısaltır.
18. **Kullanıcı kararıyla:** tamamlanma raporlarda görünmez; gönderi tamamlandığı değerle dondurulur (yeni ölçüm
    yazılmaz); ara ölçümler 90 gün sonra budanır (ilk + ay sonu + son kalır). Sentetik testte aylık toplamların
    değişmediği doğrulandı (16 ölçüm → 3).
19. **Dokümantasyon:** `docs/SISTEM_DOKUMANI.md` (tam teknik döküman, 15 bölüm) ve bu günlük. Hafızaya proje
    durumu, 7/14/30 günlük rapor isteği ve tercihler kaydedildi.

---

## Şu anki durum

| | |
|---|---|
| Otomasyon | "IG Snapshot" her gece 23:30 · "IG Bot" sürekli (oturum açılışında) |
| Hesaplar | 5 kendi hesabı (`[biz]`); rakipler şeften gelecek (`[rakipler]`) |
| Token | Local Page token; expiry is checked at runtime and never published |
| Çıktılar | `reports/YYYY-MM/` (rapor + gunluk + csv) · `reports/genel/` · `reports/kanallar/` · Telegram |
| Repo | GitHub public (README İngilizce, kullanıcı yönetiyor); son kod değişiklikleri **commit edilmedi** |

## Açık işler

- **7 / 14 / 30 günlük raporlar** — yapı tartışılacak (sorular `SISTEM_DOKUMANI.md` §15)
- README'ye yeni komutların eklenmesi (`bot`, `ask`, `limit-test`, `month-summary`, `register_bot.ps1`, yeni `.env` anahtarları)
- Kod değişikliklerinin commit'i
- Şefin rakip listesi gelince kapasiteye göre `TRACK_DAYS` / `STOP_RATIO` gözden geçirme
- Vercel / yerel panel fikri (ertelendi; veri PC'de kalacak)
