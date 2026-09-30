# Instagram Rakip Takip Sistemi — Yönetim Özeti

29.09.2026

## Amaç

Instagram, geçmiş performans verisini yalnızca sınırlı bir süre geriye dönük gösterir (Instagram Studio ve resmî
API'de eski dönem verisi alınamaz). Bu sistem, seçilen hesapların (kendi kanallarımız + rakipler) günlük
performansını **her gece kaydeder**, böylece bir yıl sonra "geçen yıl bu ay/bu hafta ne oldu" sorusuna
eksiksiz cevap verebiliriz. Veri biriktikçe değeri artar; bugün başlamazsak geriye dönük telafisi yoktur.

---

## 1. Veri nerede tutuluyor?

### Tek bir kaynak dosya

| | |
|---|---|
| Dosya | `IG-snapshot\data\ig_snapshot.db` |
| Biçim | SQLite — tek dosyalık, standart bir veritabanı (Excel gibi açılabilir, herhangi bir araçla okunabilir) |
| Konum | Şirket bilgisayarındaki proje klasörü |
| Boyut | Bugün 2,6 MB · 30 kanalla yıllık tahmin ~100 MB |
| Silme | **Yok.** Kaydedilen hiçbir ölçüm silinmez, üzerine yazılmaz |

Dışarıda hiçbir servis kullanılmıyor: bulut veritabanı, abonelik, üçüncü taraf analiz aracı yok. Veri
şirketin kendi makinesinde duruyor.

### Ne kaydediliyor — her gece 23:30

Sistem her gece her hesap için **o günün fotoğrafını** çekip veritabanına ekler:

| Tablo | Ne tutar | Örnek satır |
|---|---|---|
| `profile_snapshots` | **Her hesap için her gün bir satır**: takipçi sayısı, takip edilen, toplam gönderi | `27.09.2026 · hesap A · 537.413 takipçi` |
| `media_snapshots` | **Her gönderi için her gün bir satır**: izlenme, beğeni, yorum | `27.09.2026 · gönderi #123 · 74.322 izlenme · 2.106 beğeni` |
| `media` | Gönderinin kimliği: türü (Reels/Fotoğraf/Carousel), yayın tarihi, açıklama, linki | |
| `accounts` | Hesap listesi, son başarılı çekim, varsa hata | |
| `runs` | Her gecenin çalışma kaydı (kaç hesap başarılı/hatalı) | |

Bugüne kadar biriken: **12 günlük kayıt** (17–28 Eylül), 49 profil ölçümü, 2.176 gönderi, **11.015 gönderi ölçümü**.

### Bir örnek: 27 Eylül gecesi kaydedilenler

```
  hesap A   takipçi 537.413   ·  o gün ölçülen 165 gönderi
  hesap B   takipçi 340.329   ·  o gün ölçülen  95 gönderi
  hesap C   takipçi 326.284   ·  o gün ölçülen 127 gönderi
  hesap D   takipçi 187.991   ·  o gün ölçülen 154 gönderi
```

Yani sadece "günün özeti" değil, **her gönderinin o günkü değeri** ayrı ayrı saklanıyor. Bir videonun
gün gün nasıl büyüdüğü bir yıl sonra bile tek tek görülebiliyor.

### Video takibi ne kadar sürüyor?

Bir gönderi yayınlandıktan sonra izlenmesi artmayı bırakana kadar (tipik olarak 5–15 gün, en fazla 45 gün)
her gece ölçülür. Artış sönünce takip biter ve **o andaki değer nihai değer olarak kabul edilir**; aylık
raporda bu nihai değer yer alır ve bir daha değişmez. Ölçüm geçmişi yine saklanır.

### Veriden üretilenler

Ham veriden her gece otomatik olarak üretilen dosyalar (`IG-snapshot\reports`):

| Dosya | İçerik |
|---|---|
| Aylık rapor (Excel + özet metin) | Hesap bazında takipçi artışı, içerik sayısı, Reels/Feed kırılımı, en çok izlenen içerikler |
| Günlük içerik raporu (Excel) | Hangi gün ne üretildi, o içerikler ne kadar izlenme/beğeni topladı |
| Genel görünüm (Excel, grafikli) | Aylar arası karşılaştırma, takipçi artış eğrisi |
| Kanal dosyaları (Excel) | Her hesap için gün gün tablo ve gönderi × gün matrisi |

Ayrıca her gece Telegram'a özet mesaj, her Pazar haftalık rapor, her ay başı ay kapanışı gönderiliyor;
Telegram üzerinden "dün ne oldu", "bu hafta reels" gibi sorular anında cevaplanıyor.

### Ay sonunda veriye erişim

Her ay bittiğinde, yeni ayın ilk gecesinde biten ayın **ham verisi otomatik olarak dışa aktarılır**:

```
IG-snapshot\reports\disa-aktarim\2026-09\
    profil-olcumleri.csv       her hesabın her günkü takipçi sayısı
    gonderiler.csv             o ay yayınlanan tüm içerikler ve ulaştıkları nihai değerler
    gonderi-olcumleri.csv      gün gün tüm ölçümler (ham veri)
    veri-2026-09.xlsx          aynı üç tablo tek Excel dosyasında
    ig_snapshot-20261001.db    veritabanının o günkü dondurulmuş kopyası (aylık arşiv)
    OKUBENI.txt                sütun açıklamaları
```

Bu klasör olduğu gibi kopyalanabilir, e-postayla gönderilebilir, başka bir analistin eline verilebilir.
İstenildiği an elle de alınabilir: `python -m ig_snapshot export --month 2026-09 --db`

**Önemli:** Raporlar veritabanından her gece yeniden üretilir. Bir rapor silinse bile veri kaybolmaz;
istenen her dönem için istenen formatta rapor tekrar üretilebilir.

---

## 2. API erişimi (token) — süre sorunu ve çözümü

### Mevcut durum

| | |
|---|---|
| Erişim | Meta (Facebook) resmî Instagram Graph API — kendi şirket uygulamamız üzerinden |
| Token türü | Sayfa tokenı (SD Panel V2 uygulaması) |
| Geçerlilik | **26.10.2026** — bugün itibarıyla 27 gün |
| Yenilenebilir mi | **Evet.** Süresi dolduğunda yenisi alınabilir; veri kaybı olmaz |

### Süresi dolarsa ne olur?

Sistem o geceden itibaren veri çekemez ve Telegram'a "token geçersiz" alarmı gönderir. Kayıtlı veriler
etkilenmez. Token yenilendiği anda toplama kaldığı yerden devam eder; yalnızca token'ın geçersiz kaldığı
günlerin takipçi sayıları geri getirilemez (gönderi metrikleri kümülatif olduğu için telafi edilir).

Uyarı mekanizması: bitime **5 gün** kala her gece Telegram'a hatırlatma gider.

### Kalıcı çözüm (öneri)

Meta'nın kuralı şu: *uzun ömürlü* bir kullanıcı tokenından türetilen sayfa tokenı **süresizdir**
("Long-lived Page access tokens do not have an expiration date"). Mevcut tokenımız kısa ömürlü bir
kullanıcı tokenından türetildiği için 60 günde bitiyor. Doğru zincirle üretildiğinde bir daha
yenileme gerekmez.

Bunun için gereken tek şey **uygulama gizli anahtarı** (App Secret) — Meta uygulama panelinde
Ayarlar → Temel bölümünde. Sisteme bu bilgi girildiğinde `token-setup` komutu zinciri otomatik kurar:

```
Kullanıcı tokenı (Graph API Explorer)
        ↓  uygulama kimliği + gizli anahtar
Uzun ömürlü kullanıcı tokenı (60 gün)
        ↓
Sayfa tokenı  →  SÜRESİZ (yenileme gerekmez)
```

Token yalnızca şu durumlarda geçersiz olur: şifre değişikliği, uygulama izinlerinin kaldırılması,
hesabın uygulamadan çıkarılması. Bu durumlarda aynı komutla 5 dakikada yenisi kurulur.

**Karar gereken:** Uygulama gizli anahtarının sisteme girilmesi (bilgisayarda, `.env` dosyasında,
GitHub'a gönderilmeyen gizli alanda tutulur). Onay verilirse token sorunu kalıcı olarak kapanır.

---

## 3. Riskler ve öneriler

| Risk | Bugünkü durum | Öneri |
|---|---|---|
| **Token süresi** | 27 gün kaldı, elle yenilenebilir | App Secret ile süresiz tokena geçiş (yukarıda) |
| **Bilgisayar kapalı olursa** | O gecenin takipçi verisi alınamaz, telafisi yok | Bilgisayarın gece açık kalması; ek olarak GitHub Actions üzerinde ücretsiz ikinci bir toplayıcı |
| **Disk arızası** | Veri tek makinede, yedeği yok | Her gece OneDrive ve/veya şirket GitHub hesabına otomatik yedek |
| **Hesap adı değişimi / kapanması** | Sistem tanıyıp uyarıyor, geçmişi koruyor | Uyarı geldiğinde listenin güncellenmesi |

Maliyet: **sıfır**. Resmî API ücretsiz kotayla kullanılıyor, ek yazılım/abonelik yok.

---

## 4. Kapsam

- **Şu an izlenen:** 4 kendi kanalımız (1 kanal banlandığı için pasif)
- **Eklenecek:** rakip kanal listesi — sistem 20–30 kanalı mevcut kotayla sorunsuz taşır
- **Toplanan veriler:** takipçi, gönderi sayısı, içerik türü (Reels/Fotoğraf/Carousel), izlenme, beğeni, yorum,
  yayın zamanı, içerik linki
- **Toplanamayanlar (API vermiyor):** story verileri, kaydetme/paylaşım sayıları, erişim (reach),
  fotoğraf ve carousel içeriklerinin izlenme sayısı. Yalnızca işletme/içerik üretici hesapları sorgulanabilir.
- Sistem yalnızca **okuma** yapar; hiçbir hesapta paylaşım, beğeni, takip gibi işlem yapmaz.
