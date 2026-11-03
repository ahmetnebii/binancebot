# binancebot

Binance USDⓈ-M Futures için **risk öncelikli, tam otomatik** işlem sistemi ve profesyonel kontrol paneli.

> [!WARNING]
> Kaldıraçlı işlemler yüksek risk içerir; yatırdığın paranın tamamını kaybedebilirsin. Bu proje kazanç garantisi vermez. Önce **Simülasyon**, sonra **Testnet**, en son küçük sermayeyle **Canlı** kullan.

📘 Tasarım ve gerekçeler: [YAPIM_KILAVUZU.md](YAPIM_KILAVUZU.md) · 🔌 API: [docs/API.md](docs/API.md)

## Neler yapar?

- **Tüm USDT perpetual kontratları** gerçek zamanlı tarar. Binance'in 2026 WebSocket yapısını (`/market`, `/public`, `/private`) kullanır.
- **Fırsat stratejileri:**
  - S1 Trend Kırılımı (15 dk)
  - S2 Göreli Güç Rotasyonu (1 saat)
  - Piyasa rejimi filtresi
  - Her fırsat için puan ve tahmini beklenti (EV) hesaplar.
- **Kasa yönetimi (sermayeye oranlı):**
  - İşlem riski özsermayenin yüzdesidir.
  - Kaldıracı sistem önerir: büyüklüğü marjin bütçesine sığdıran en düşük değer, likidasyon stop'tan en az 3 kat uzakta.
  - Portföy, korelasyon ve marjin limitleri, devre kesiciler.
- **Otomatik mod aç/kapa:** Kapalıyken fırsatlar panele düşer, sen tek tıkla işlem açarsın. Ayrıca istediğin sembolde **manuel işlem bileti** var.
- **Pozisyon detayı:**
  - Canlı grafik, SL/TP bölgeleri, likidasyon çizgisi.
  - **Tahmini ilerleme:** volatilite konisi, ivme yolu, hedef olasılığı, tahmini süre.
- **Borsada duran stop** (Algo API, MARK_PRICE), TP merdiveni, başa-baş, iz süren stop, zaman stopu ve otomatik mutabakat.
- **Ortamlar:** Simülasyon (gerçek veri + sanal emir) · Testnet · Canlı.
- **Ayarlar arayüzden:** API anahtarları ve Telegram token sunucuda **şifreli** saklanır, tarayıcıya asla geri gönderilmez.
- **Telegram:** Bildirimler ve komutlar (`/durum`, `/pozisyonlar`, `/otomatik_ac`, `/otomatik_kapat`, `/durdur`, `/devam`).
- **Ücretli API yok:** Yalnızca Binance'in ücretsiz API'leri ve açık kaynak yazılım kullanılır.

## Hızlı başlangıç (Docker — önerilen)

Sunucuda (sabit IP'li bir VPS önerilir; ABD bölgeleri Binance tarafından engellenir):

```bash
git clone https://github.com/ahmetnebii/binancebot.git
```

```bash
cd binancebot
```

```bash
docker compose up -d --build
```

Panel güvenlik için yalnızca sunucunun kendisinde (`127.0.0.1:8000`) açılır. Kendi bilgisayarından SSH tüneliyle bağlan:

```bash
ssh -L 8000:127.0.0.1:8000 kullanici@SUNUCU_IP
```

Sonra tarayıcıda **http://localhost:8000** adresini aç.

## İlk kurulum

1. **Parola oluştur:** İlk açılışta panel parolası istenir (en az 10 karakter).
2. **Simülasyonla başla:** Varsayılan ortam budur ve anahtar gerekmez. Başlangıç bakiyesini **Ayarlar → Simülasyon** bölümünden ayarla. Sistem gerçek piyasa verisiyle sanal işlem yapar.
3. **Testnet:** Binance demo/testnet hesabından anahtar al. **Ayarlar → Bağlantı → Testnet** bölümünde anahtarları gir ve **Bağlantıyı test et** butonuna bas.
4. **Canlı:** Yeni bir API anahtarı oluştur. **Çekim yetkisi KAPALI**, **IP kısıtlaması AÇIK** (sunucunun IP'si) olmalı. **Ayarlar → Bağlantı → Canlı** bölümüne gir ve onay için `CANLI` yaz.
5. **Telegram:**
   - @BotFather'dan bot oluştur ve token'ı **Ayarlar → Telegram** bölümüne kaydet.
   - Bota bir mesaj gönder, sonra **Chat ID bul** butonuna bas.
   - **Test mesajı** gönderip çalıştığını doğrula.
6. **Otomatik mod:** Üst çubuktaki anahtarla aç veya kapat. Ortam değiştiğinde güvenlik için kendiliğinden kapanır.

## Geliştirme (Docker'sız)

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
```

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

```bash
cd backend && ../.venv/bin/python -m app
```

Arayüz (ayrı terminalde; `/api` ve `/ws` istekleri 8000 portuna yönlendirilir):

```bash
cd frontend && npm install && npm run dev
```

Binance'e erişimin olmadığı bir ortamda arayüzü denemek için **yalnızca geliştirme amaçlı** sentetik veri kullanılabilir. Bu veri gerçek piyasa değildir. Panelde kalıcı bir uyarı gösterilir ve sistem zorunlu olarak simülasyonda kalır:

```bash
cd backend && BB_DATA_SOURCE=synthetic ../.venv/bin/python -m app
```

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `BB_DATA_DIR` | `./data` | Veritabanı ve `master.key` (birlikte yedekle) |
| `BB_HOST` / `BB_PORT` | `127.0.0.1` / `8000` | Dinlenen adres |
| `BB_MASTER_KEY` | — | Şifreleme ana anahtarı (verilmezse `data/master.key` üretilir) |
| `BB_COOKIE_SECURE` | `0` | HTTPS ters proxy arkasında `1` |
| `BB_DATA_SOURCE` | `binance` | `synthetic` yalnızca geliştirme içindir |

## Güvenlik notları

- `data/master.key` kaybolursa kayıtlı anahtarlar çözülemez ve yeniden girilmeleri gerekir. `data/` klasörünü yedekle ama asla Git'e ekleme.
- Paneli internete açacaksan HTTPS (ör. Caddy) ve güçlü bir parola kullan; `BB_COOKIE_SECURE=1` ayarla.
- Kaydedilen API anahtarı ve token arayüze bir daha gösterilmez, sadece son 4 karakteri görünür.

## Durum ve bilinen sınırlamalar (v0.1)

- Binance bağlantı katmanı resmi API formatlarına göre yazıldı ve birim testleriyle doğrulandı. Ancak gerçek Binance'e karşı ilk uçtan uca test **senin sunucunda** yapılacak, çünkü geliştirme ortamının Binance'e erişimi yoktu. Sıra: Simülasyon → Testnet → küçük Canlı.
- Testnet/demo adresleri ve WebSocket yolları **Ayarlar → Bağlantı → Gelişmiş** bölümünden değiştirilebilir.
- Strateji parametreleri henüz backtest ile doğrulanmadı (kılavuzda Faz 4). Beklenti tahminleri varsayılan istatistiklerle başlar ve gerçek sonuçlarla güncellenir.
