# API Sözleşmesi (backend ↔ arayüz)

Backend: FastAPI (`backend/app`). Arayüz aynı origin'den servis edilir (`frontend/dist`).
Geliştirmede Vite dev sunucusu `/api` ve `/ws` isteklerini `http://127.0.0.1:8000`'e yönlendirir.

## Genel kurallar

- Tüm gövdeler JSON'dur.
- **Kimlik doğrulama:** HttpOnly çerez `bb_session`. `/api/auth/*` dışındaki tüm uç noktalar oturum ister. Oturum yoksa `401` döner.
- **CSRF koruması:** Veri değiştiren isteklerde (POST/PUT/DELETE) `X-BB-Client: ui` başlığı zorunludur.
- **Hatalar:** HTTP 4xx/5xx ve `{"detail": "Türkçe açıklama"}`.
- **Zaman birimleri:**
  - Tüm `*_at`, `ts`, `server_time` alanları **milisaniyedir** (Unix ms).
  - Grafik verisinde (`Candle.time`, `cone[].time`, `momentum_path[].time`, equity `time`) birim **saniyedir** (lightweight-charts `UTCTimestamp`).
- **Oran alanlarının birimi:**

  | Birim | Alanlar |
  |---|---|
  | **Kesir** (0.0075 = %0,75) | `SizingPlan.risk_pct`; tüm ayar oranları (`base_risk_pct`, `daily_loss_pct`, `max_total_margin_pct`…); `Signal.win_prob`; `TakeProfit.fraction`; `Regime.breadth`, `Regime.vol_pct`; `stats.win_rate` |
  | **Yüzde** (1.25 = %1,25) | `Position.roe_pct`, `stop_distance_pct`, `liq_distance_pct`; `SizingPlan.stop_distance_pct`, `liq_distance_pct`; `Equity.margin_used_pct`, `open_risk_pct`, `day_pnl_pct`, `drawdown_pct`; `/market/symbols` → `change_pct`; `Signal.features.atr_pct` |

- **Ortamlar:**
  - `paper`: Simülasyon. Gerçek piyasa verisi kullanılır, emirler sanaldır.
  - `testnet`: Binance demo/testnet.
  - `live`: Gerçek hesap.

## Tipler (TypeScript)

```ts
type Env = "paper" | "testnet" | "live";
type Side = "LONG" | "SHORT";

interface TakeProfit {
  r: number;               // R katı (ör. 1.5)
  price: number;
  fraction: number;        // kapatılacak oran (0-1)
  filled: boolean;
  filled_at: number | null;
}

interface SizingPlan {
  ok: boolean;
  reason: string;          // ok=false ise ret nedeni
  notional: number;
  qty: number;
  leverage: number;
  margin: number;
  risk_usdt: number;
  risk_pct: number;        // 0.0075 = %0,75
  stop_distance_pct: number;
  liq_price: number;
  liq_distance_pct: number;
  fee_estimate: number;
  notes: string[];
  multipliers: Record<string, number>;  // kalite, düşüş, seri, rejim, sermaye
}

interface Signal {
  id: string;
  symbol: string;
  side: Side;
  strategy: string;        // "S1" | "S2" | "M" (manuel)
  strategy_name: string;   // "Trend Kırılımı"
  timeframe: string;       // "15m"
  created_at: number;
  expires_at: number;
  bar_time: number;
  entry: number;
  stop: number;
  take_profits: TakeProfit[];
  score: number;           // 0-100
  ev_r: number;            // tahmini beklenti (R)
  win_prob: number;        // 0-1
  rr: number;              // son hedefin R katı
  reasons: string[];
  warnings: string[];
  features: Record<string, number>;
  status: "ACTIVE" | "EXECUTED" | "DISMISSED" | "EXPIRED" | "REJECTED" | "FAILED";
  status_note: string;
  plan: SizingPlan | null; // güncel öneri: büyüklük, kaldıraç, marjin
  position_id: string | null;
  price: number | null;    // anlık fiyat
}

interface PositionEvent {
  ts: number;
  kind: string;            // open | tp | sl | stop_move | breakeven | trailing | partial_close | close | funding | info | warning
  message: string;
  price: number | null;
  qty: number | null;
}

interface Position {
  id: string;
  env: Env;
  symbol: string;
  side: Side;
  strategy: string;
  strategy_name: string;
  signal_id: string | null;
  source: "auto" | "manual";
  status: "OPEN" | "CLOSED";
  opened_at: number;
  closed_at: number | null;
  timeframe: string;
  entry_price: number;
  qty: number;             // kalan miktar
  initial_qty: number;
  leverage: number;
  margin: number;
  notional: number;
  stop_price: number;
  initial_stop: number;
  take_profits: TakeProfit[];
  trailing_active: boolean;
  liq_price: number;
  time_stop_at: number | null;
  realized_pnl: number;
  fees: number;
  funding: number;
  exit_price: number | null;
  close_reason: string | null;
  risk_usdt: number;
  mfe_r: number;
  mae_r: number;
  r_final: number | null;
  events: PositionEvent[];
  // anlık (runtime) alanlar
  mark_price: number | null;
  unrealized_pnl: number;
  pnl_total: number;       // realized + unrealized - fees + funding
  roe_pct: number;         // marjine göre %
  r_now: number;           // anlık R
  liq_distance_pct: number | null;
  stop_distance_pct: number | null;
}

interface Projection {
  timeframe: string;
  sigma_bar: number;                 // bar başına log-getiri std
  horizon_bars: number;
  target_price: number | null;       // sıradaki hedef (doldurulmamış ilk TP)
  target_label: string | null;       // "TP1"
  p_target_first: number | null;     // hedefe stop'tan önce ulaşma olasılığı (yönsüz model)
  p_target_first_momentum: number | null;  // ivme varsayımıyla
  expected_bars: number | null;      // hedef veya stop'tan birine kadar beklenen bar (yönsüz)
  eta_bars_momentum: number | null;  // mevcut ivmeyle hedefe tahmini bar
  cone: { time: number; up1: number; dn1: number; up2: number; dn2: number }[];
  momentum_path: { time: number; price: number }[];
}

interface PositionDetail { position: Position; projection: Projection | null; }

interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number; }

interface Equity {
  equity: number;
  wallet: number;
  unrealized: number;
  available: number;
  margin_used: number;
  margin_used_pct: number;
  open_risk: number;
  open_risk_pct: number;
  day_pnl: number;
  day_pnl_pct: number;
  hwm: number;
  drawdown_pct: number;
}

interface Regime {
  name: "BULL" | "BEAR" | "RANGE" | "CHAOS" | "UNKNOWN";
  label: string;           // "Boğa trendi"
  risk_mult: number;
  btc_trend: string;
  breadth: number | null;  // 0-1
  vol_pct: number | null;  // 0-1
  updated_at: number | null;
}

interface Breaker {
  code: string;
  label: string;
  active: boolean;
  detail: string;
  until: number | null;
}

interface Status {
  env: Env;
  data_source: "binance" | "synthetic";   // synthetic = SADECE geliştirme verisi
  auto_mode: boolean;
  paused: boolean;
  paused_reason: string;
  connection: {
    market: "ok" | "connecting" | "down";
    user_stream: "ok" | "connecting" | "down" | "n/a";
    broker: "ok" | "error" | "no_keys" | "starting";
    message: string;
  };
  equity: Equity | null;
  regime: Regime;
  breakers: Breaker[];
  counts: { open_positions: number; active_signals: number; universe: number };
  scanner: { last_scan_at: number | null; next_scan_at: number | null; scanned_symbols: number };
  limits: { max_positions: number; effective_max_positions: number; base_risk_pct: number };
  stats: {
    trades: number;
    win_rate: number | null;
    expectancy_r: number | null;
    profit_factor: number | null;
    total_pnl: number;
  };
  server_time: number;
  version: string;
}

interface EventItem {
  id: number;
  ts: number;
  level: "info" | "success" | "warning" | "error";
  kind: string;
  message: string;
}
```

## Uç noktalar

### Kimlik doğrulama

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/auth/state` | | `{setup_required: boolean, authenticated: boolean}` |
| POST | `/api/auth/setup` | `{password}` (en az 10 karakter; sadece ilk kurulumda) | `{ok: true}` + çerez |
| POST | `/api/auth/login` | `{password}` | `{ok: true}` + çerez |
| POST | `/api/auth/logout` | | `{ok: true}` |

### Durum ve kontrol

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/status` | | `Status` |
| POST | `/api/control/auto` | `{enabled: boolean}` | `Status` |
| POST | `/api/control/pause` | `{paused: boolean, reason?: string}` | `Status` |
| POST | `/api/control/close-all` | | `{closed: number}` |

### Fırsatlar (sinyaller)

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/signals?scope=active\|recent` | | `Signal[]` |
| POST | `/api/signals/{id}/preview` | `{risk_pct?: number, leverage?: number}` | `{plan: SizingPlan, entry: number, take_profits: TakeProfit[]}`: güncel fiyatla, işleme ile **aynı kurallarla** plan |
| POST | `/api/signals/{id}/execute` | `{risk_pct?: number, leverage?: number}` | `Position` |
| POST | `/api/signals/{id}/dismiss` | | `Signal` |

### Pozisyonlar

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/positions?status=open\|closed&limit=100` | | `Position[]` |
| GET | `/api/positions/{id}` | | `PositionDetail` |
| POST | `/api/positions/{id}/close` | `{fraction: number}` (0–1] | `Position` |
| POST | `/api/positions/{id}/stop` | `{price: number}` | `Position` |
| POST | `/api/positions/{id}/breakeven` | | `Position` |
| POST | `/api/positions/{id}/take-profits` | `{take_profits: {price: number, fraction: number}[]}` | `Position` |

### Manuel emir

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| POST | `/api/orders/preview` | `ManualOrder` | `{plan: SizingPlan, entry: number, take_profits: TakeProfit[]}` |
| POST | `/api/orders/manual` | `ManualOrder` | `Position` |

```ts
interface ManualOrder {
  symbol: string;
  side: Side;
  stop_price: number;                                  // zorunlu: stopsuz işlem yok
  take_profits?: { r?: number; price?: number; fraction: number }[];  // boşsa varsayılan merdiven
  risk_pct?: number;                                   // boşsa sistem riski
  leverage?: number;                                   // boşsa sistem önerisi (yine güvenlik tavanlarına tabi)
}
```

### Piyasa ve geçmiş

| Metot | Yol | Yanıt |
|---|---|---|
| GET | `/api/market/symbols` | `{symbol, price, change_pct, quote_volume, funding_rate, in_universe}[]` |
| GET | `/api/market/klines?symbol=BTCUSDT&interval=15m&limit=300` | `Candle[]` (aralık: `1m`, `5m`, `15m`, `1h`, `4h`) |
| GET | `/api/history/equity?range=1d\|7d\|30d\|all` | `{time: number /*sn*/, equity: number}[]` |
| GET | `/api/history/stats` | `{trades, wins, losses, win_rate, expectancy_r, profit_factor, total_pnl, avg_win_r, avg_loss_r, best_r, worst_r, by_strategy: {strategy, strategy_name, trades, win_rate, total_pnl, expectancy_r}[], r_histogram: RBin[], monthly: {month: "2026-11", pnl: number, trades: number}[]}` |
| GET | `/api/events?limit=200` | `EventItem[]` |
| GET | `/api/market/analysis?symbol=BTCUSDT` | `SymbolAnalysis` (aşağıda) |

```ts
interface RBin { from: number; to: number; count: number }   // R aralığı [from, to)

interface TfAnalysis {
  tf: "15m" | "1h" | "4h";
  trend: "up" | "down" | "flat";        // EMA20/50/200 dizilimi + fiyatın konumu
  ema_stack: string;                     // ör. "Fiyat > EMA20 > EMA50 > EMA200"
  rsi: number | null;                    // 0-100
  adx: number | null;
  atr_pct: number | null;                // yüzde (ATR / fiyat × 100)
  bb_pctb: number | null;                // Bollinger %B (0 = alt bant, 1 = üst bant)
  dist_ema20_atr: number | null;         // (fiyat − EMA20) / ATR
  vol_z: number | null;                  // log-hacim z-skoru (son bar)
  change_pct: number | null;             // son 24 barlık değişim, yüzde
}

interface SymbolAnalysis {
  symbol: string;
  price: number | null;
  updated_at: number;
  timeframes: TfAnalysis[];              // 15m, 1h, 4h sırasıyla (veri yoksa eksik olabilir)
  confluence: { long: number; short: number; label: string };   // 0-3: kaç zaman dilimi aynı yönde
  derivatives: {
    funding_rate: number | null;         // kesir (0.0001 = %0,01 / 8 saat)
    next_funding_time: number | null;    // ms
    oi_change_1h_pct: number | null;     // yüzde
    liq_long_1h: number | null;          // son 1 saatte likide edilen long'lar (USDT)
    liq_short_1h: number | null;
  };
  market: {
    change_24h_pct: number | null;       // yüzde
    quote_volume_24h: number | null;
    volume_rank: number | null;
    btc_corr: number | null;             // 1 saatlik getirilerle, -1..1
    btc_beta: number | null;
    rs_24h_pct: number | null;           // BTC'ye göre 24s göreli getiri, yüzde
  };
  liquidity: { bid: number | null; ask: number | null; spread_bps: number | null };
  regime: Regime;
  notes: string[];                       // Türkçe özet cümleler
}
```

### Geriye dönük test (backtest)

Aynı strateji, rejim ve risk kodu geçmiş veride çalıştırılır. Veri Binance REST'ten çekilip `data/history` altında önbelleğe alınır (sentetik modda sentetik geçmiş üretilir). Aynı anda tek iş çalışır; ilerleme WS `backtest` mesajıyla da yayınlanır.

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| POST | `/api/backtest/run` | `BacktestConfigIn` | `BacktestJob` |
| GET | `/api/backtest/jobs` | | `BacktestJob[]` (son 20, `result` olmadan) |
| GET | `/api/backtest/jobs/{id}` | | `BacktestJob` (`result` ile) |
| POST | `/api/backtest/jobs/{id}/cancel` | | `BacktestJob` |
| DELETE | `/api/backtest/jobs/{id}` | | `{ok: true}` |
| POST | `/api/backtest/jobs/{id}/apply-calibration` | | `Calibration` |
| GET | `/api/backtest/calibration` | | `Calibration` |
| DELETE | `/api/backtest/calibration` | | `Calibration` (varsayılana döner) |

```ts
interface BacktestConfigIn {
  days?: number;                 // 7-365, varsayılan 90
  top_n?: number;                // 5-80, varsayılan 25 (hacme göre en büyükler; BTCUSDT her zaman dahil)
  symbols?: string[];            // verilirse top_n yerine bunlar
  strategies?: { s1?: boolean; s2?: boolean; s3?: boolean };   // varsayılan: mevcut ayarlar
  initial_equity?: number;       // varsayılan 1000
  slippage_bps?: number;         // varsayılan simülasyon ayarı
  taker_fee?: number; maker_fee?: number;
}

interface BacktestSummary {
  trades: number; win_rate: number | null;          // kesir
  profit_factor: number | null; expectancy_r: number | null;
  total_return_pct: number; max_drawdown_pct: number;   // yüzde
  sharpe: number | null;
  period_start: number; period_end: number;          // ms
  symbols: number;
}

interface BacktestJob {
  id: string;
  created_at: number; finished_at: number | null;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: { phase: "data" | "simulate" | "analyze" | "done"; pct: number; message: string };
  config: Required<Omit<BacktestConfigIn, "symbols">> & { symbols: string[] };
  error: string | null;
  summary: BacktestSummary | null;
  result?: BacktestResult;
}

interface BacktestResult {
  summary: BacktestSummary & {
    cagr_pct: number | null; sortino: number | null; calmar: number | null;
    avg_win_r: number | null; avg_loss_r: number | null; best_r: number | null; worst_r: number | null;
    avg_hold_hours: number | null; exposure_pct: number; trades_per_day: number;
    fees_total: number; funding_total: number; initial_equity: number; final_equity: number;
  };
  by_strategy: { strategy: string; strategy_name: string; trades: number; win_rate: number | null;
                 profit_factor: number | null; expectancy_r: number | null; total_pnl: number;
                 avg_win_r: number | null; avg_loss_r: number | null }[];
  by_side: { side: Side; trades: number; win_rate: number | null; expectancy_r: number | null; total_pnl: number }[];
  equity: { time: number /*sn*/; equity: number; drawdown_pct: number }[];
  monthly: { month: string; return_pct: number }[];
  r_histogram: RBin[];
  monte_carlo: { runs: number; dd_p50: number; dd_p95: number; dd_p99: number;     // yüzde
                 final_p5: number; final_p50: number; final_p95: number;           // özsermaye (USDT)
                 prob_dd_20: number; prob_dd_30: number } | null;                    // kesir
  walk_forward: { fold: number; start: number; end: number; trades: number;
                  expectancy_r: number | null; profit_factor: number | null; return_pct: number }[];
  trades: { symbol: string; side: Side; strategy: string; entry_time: number; exit_time: number;
            entry_price: number; exit_price: number; r: number; pnl: number; fees: number;
            reason: string; hold_bars: number }[];            // en fazla 2000
  calibration: Record<string, { n: number; win_rate: number; avg_win_r: number; avg_loss_r: number; expectancy_r: number }>;
  data_source: "binance" | "synthetic";
  warnings: string[];
}

interface Calibration {
  source_job: string | null;
  applied_at: number | null;
  strategies: Record<string, { n: number; win_rate: number; avg_win_r: number; avg_loss_r: number; expectancy_r: number }>;
}
```

Kalibrasyon uygulandığında fırsatların kazanma olasılığı ve beklenti (EV) tahmini, varsayılan değerler yerine bu ölçülmüş istatistiklerle (canlı sonuçlarla harmanlanarak) hesaplanır.

**İşaret notu:** `calibration[*].avg_loss_r` kaybın **pozitif büyüklüğüdür** (ör. 0.94 = ortalama −0,94R); `summary.avg_loss_r` ve `by_strategy[].avg_loss_r` ise **negatif** R değeridir.

### Otomatik ayar bulucu (optimize)

Tek tuşla parametre araması. Dönem ikiye bölünür: ilk 2/3 **eğitim** (adaylar burada t-istatistiğine göre sıralanır), son 1/3 **doğrulama** (hiç görülmemiş veri; karar yalnızca burada verilir). En iyi 6 aday doğrulamada sınanır. Karar eşikleri: `recommended` ≥15 işlem, beklenti ≥0,10R, PF ≥1,3; `weak` ≥10 işlem, beklenti >0, PF >1; aksi `none`. Backtest ile aynı kuyruğu paylaşır (aynı anda tek iş).

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| POST | `/api/backtest/optimize` | `{days?: 60–365 (180), top_n?: 5–80 (30), budget?: "hizli"\|"normal"\|"derin"}` | `OptimizeJob` (`kind: "optimize"`, id `opt_…`) |
| GET | `/api/backtest/optimize/jobs` | — | `OptimizeJob[]` (sonuçsuz) |
| GET | `/api/backtest/jobs/{id}` | — | Tam iş; `result: {verdict, verdict_text, period{start,split,end}, candidates_tested, best, baseline, leaderboard[], full, calibration, data_source, warnings}` |
| POST | `/api/backtest/jobs/{id}/cancel` | — | İptal |
| POST | `/api/backtest/optimize/{id}/apply` | — | `{ok, applied, calibrated}`; strateji ayarları + yön izinleri + `risk.min_ev_r` güncellenir, kalibrasyon yazılır, işe `applied_at` eklenir. `verdict: "none"` → 400 |

Strateji ayarlarındaki yön izinleri: `s1_sides`, `s2_sides`, `s3_sides`, `s4_sides` ∈ `both | long | short` (canlı tarama ve backtest ikisi de uyar).

Arama iki aşamalıdır: (1) yapı taraması — 10 strateji seti × {iki yön, sadece long} × {normal, sıkı rejim}, varsayılan parametrelerle; (2) en iyi 3 yapının etrafında parametre inceltme. Bütçe: hizli 24, normal 48, derin 96 aday. Eğitim puanı = işlem R'lerinin t-istatistiği − düşüş cezası − tutarlılık cezası (işlemler 3 zaman parçasına bölünür, en az 2'si kârlı olmalı).

Yeni strateji ayarları:
- `s4_enabled`, `s4_donchian` (10–120, 4 saatlik mum), `s4_atr_mult` (1–6): S4 4 saatlik trend takibi, 4 saatlik kapanışlarda değerlendirilir.
- `s5_enabled`, `s5_pivot` (3–10), `s5_tol` (0,3–2,0 ATR), `s5_vol_z` (0–3), `s5_sides`: S5 grafik formasyonları (omuz-baş-omuz ve tersi, ikili tepe/dip, yükselen/alçalan üçgen), 1 saatlik kapanışlarda; pivotlar sağda k mum oluşunca kesinleşir, giriş taze kırılımda.
- `regime_filter`: `normal | strict` — strict'te S1/S2/S4 yalnızca rejim yönünde, S3 yalnızca yatay rejimde işlem açar.
- `funding_max` (0,0001–0,005): kendi yönümüze ödenen funding bunu aşarsa giriş yapılmaz.
- `exit_profile`: `standard | runner | quick` — hedef merdiveni ve iz süren stop planı.

### Strateji Laboratuvarı (lab)

Binlerce kuralı tarayan arama. Kural = zaman dilimi (15m/1h/4h) + giriş tetikleyicisi (11 tip: kanal kırılımı, EMA kesişimi, RSI dönüşü, Bollinger dönüş/kırılım, Keltner, MACD, momentum, trendde geri çekilme, sıkışma kırılımı, hacimli kırılım) + 0–2 filtre (trend, eğim, ADX trend/yatay, hacim, BTC trendi, sakin volatilite) + stop (k×ATR) + çıkış (sabit hedef ya da kısmi hedef + iz süren stop) + zaman stopu + yön.

Süreç: dönem %60 eğitim / %20 doğrulama / %20 final sınavı. Keşif (%70 rastgele) + evrimsel iyileştirme (%30, en iyi 40 kuralın komşuları) eğitimde hızlı vektörel simülasyonla puanlanır (t-istatistiği − zaman tutarlılığı cezası − coin yaygınlığı cezası). En iyi 200 → doğrulamada kârlı olanlar → en iyi 8 finalist canlıyla aynı portföy simülatöründe (eğitim+doğrulama) → kazanan final sınavında bir kez test edilir. Şans çıtası √(2·ln N) altında kalan kurala en fazla "weak" denir. Bütçe: hizli 2.000, normal 10.000, derin 30.000, maksimum 100.000 kural.

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| POST | `/api/backtest/lab` | `{days?: 90–1095 (365), top_n?: 10–60 (30), budget?, grouped?: bool}` | `LabJob` (`kind: "lab"`, id `lab_…`) |
| GET | `/api/backtest/lab/jobs` | — | `LabJob[]` |
| GET | `/api/backtest/jobs/{id}` | — | `result: {verdict, verdict_text, period{start,split1,split2,end}, tested, luck_bar, shortlist, passed_oos, best{label, details, rule, is, oos, dev, final}, finalists[], leaderboard[], full, calibration, data_source, warnings}` |
| POST | `/api/backtest/lab/{id}/apply` | `{mode?: "live" \| "paper"}` | `live`: yalnızca doğrulanmış (recommended/weak) gruplar; `paper`: dört aşamada da kârlı adaylar DENEME olarak (otomatik işlem yalnızca Simülasyon ortamında). Tek grupta `lab_rule`, gruplu sonuçta `lab_groups` yazılır; S1–S5 kapatılır |

**Yöntemler (`mode`):** `single` (tüm coinlere tek kural), `groups` (3 coin grubu), `coins` (her coine ayrı kural; 3–30 coin). Zaman dilimleri: 15m, 1h, 4h, **1d** (günlük; canlıda günlük mumlar her gün UTC 00:00 sonrası REST ile yenilenir). Her grup/coin için `full_summary` (tüm dönem getiri, düşüş, işlem, kazanma, PF) her zaman döner; arayüz bunu "Sonuç raporu" tablosu ve CSV olarak sunar.

**Geçmiş veri önbelleği:**

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/backtest/data/status` | — | `{symbols, first, last, updated_at, size_mb}` |
| POST | `/api/backtest/data/sync` | `{days?: 30–1095, top_n?: 3–60}` | `BacktestJob` (`kind: "data"`): mumları ve funding'i indirir / yalnızca yeni kısmı ekler |

Laboratuvar araması önbelleği olduğu gibi kullanır (`update_tail=False`): ağdan yalnızca önbellekte hiç olmayan coin/dönem indirilir; önbellek eskiyse dönem eldeki son mumda biter. Her önbellek dosyası verinin hangi tarihten itibaren istendiğini (`req_from`) saklar; böylece seçilen dönemden yeni listelenmiş coinler her seferinde baştan indirilmez. Funding geçmişi de önbelleğe alınır.

**Coin grubu bazlı arama (`grouped: true`, ≥20 sembol):** coinler YALNIZCA eğitim dönemindeki verilerine göre 3 gruba ayrılır: `big` (hacimce ilk ~%20, en az 4), `mid` / `high` (kalanlar saatlik getiri oynaklığının medyanına göre). Her gruba ayrı arama yapılır (her grubun kendi şans çıtası, kendi final sınavı). Sonuç: `result.groups[] = {key, name, symbols, vol_min, vol_max, verdict, verdict_text, forward_ok, tested, luck_bar, best, finalists, leaderboard, full, calibration}`. Canlıda listede olmayan yeni bir coin, 1 saatlik oynaklığına göre `mid`/`high` grubuna atanır. Tek tek coin bazında kural aranmaz: coin başına işlem sayısı güvenilir doğrulama için yetmez.

**Karar:** final sınavında ≥15 işlem, beklenti ≥0,10R, PF ≥1,3 ve şans çıtası geçildiyse `recommended`; ≥0,05R / PF ≥1,1 ise `weak`; final getirisi ≤0 ise her durumda `none`. `forward_ok`: dört aşamanın hepsinde pozitif beklenti ve finalde ≥5 işlem (simülasyonda denemeye değer).

Geçmiş 3 yıla kadar çıkabilir; ilk indirme ~20–30 dk sürer, sonra `data/history` önbelleğinden gelir. 400 günden uzun aramalarda en fazla 4 işçi ve işçi başına ~350 MB gösterge önbelleği kullanılır.

Kural canlıda S6 olarak çalışır (`strategies/lab_rules.py`); hızlı tarama, portföy simülasyonu ve canlı tarama aynı sinyal kodunu kullanır. S6 varsayılan EV eşiğinden ve çıkış profilinden muaftır (kendi doğrulanmış çıkışını kullanır).

### Funding arbitrajı (carry)

Delta-nötr strateji: spotta al + vadelide aynı adette short → fiyat riski yok, funding toplanır. Analiz önbellekteki funding geçmişini kullanır; 8 saatlik pencereler (4 saatlik coinlerde iki ödeme toplanır). Kural: son `k` pencere ortalaması yıllık `enter_apr` üstündeyse gir, `exit_apr` altına inince çık; portföyde ortalaması en yüksek `top_n` coin (tutulan coin ilk 2N'de ve eşik üstünde kaldıkça bırakılmaz). Parametreler eğitimde (%60) seçilir, sonuç test döneminde (%40) raporlanır. Maliyet: giriş + çıkışta spot ve vadeli komisyon + iki bacak kayma; sermaye verimi 1/(1+1/kaldıraç). Karar: test yıllık ≥ %8, düşüş ≤ %5, ayların ≥ %70'i artı → `recommended`; yıllık > %3 → `weak`.

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| POST | `/api/backtest/carry` | `{days?: 90–1095, top_n?: 5–60, spot_fee?, leverage?: 1–10}` | `CarryJob` (`kind: "carry"`) |
| GET | `/api/backtest/carry/jobs` | — | `CarryJob[]` |
| POST | `/api/backtest/carry/{id}/apply` | — | Seçilen kural `kv:carry_settings`'e yazılır (canlı radar + sanal portföy bunu kullanır) |

### Kalabalık verisi ve funding radarı (`/api/crowd`)

`CrowdCollector` her saat ilk 30 coin için Binance'ten açık pozisyon (OI), tüm hesap ve büyük hesap long/short oranları, taker alım/satım oranı ve funding geçmişini çekip `crowd` ve `funding_hist` tablolarına yazar (ilk seferde ~20 gün geri doldurur; Binance bu verileri yalnızca ~30 gün geriye verir). Her funding dönemi bitince sanal funding portföyü (`kv:carry_paper`, 1.000 USDT) aynı kuralla güncellenir; coin değişikliği Telegram'a bildirilir.

| Metot | Yol | Yanıt |
|---|---|---|
| GET | `/api/crowd/status` | `{rows, symbols, first, last, funding_rows, days, state, last_run, last_error}` |
| GET | `/api/crowd/funding-radar` | `{settings, rows[{symbol, current_apr_pct, apr_7d_pct, apr_30d_pct, trailing_apr_pct, selected, held}], paper{...}}` |
| POST | `/api/crowd/collect` | Hemen topla + sanal portföyü güncelle |
| POST | `/api/crowd/paper/reset` | Sanal portföyü sıfırla |
| GET | `/api/crowd/analysis` | Her ölçüm için (kendi son 7 gününe göre z > 1 / z < −1) sonraki 24 saat getirisi, fark ve t-değeri; 60 günden az veride uyarı |

### Ayarlar

| Metot | Yol | Gövde | Yanıt |
|---|---|---|---|
| GET | `/api/settings` | | `SettingsView` |
| PUT | `/api/settings/connection` | `{env?, key_type?, api_key?, api_secret?, private_key?, clear?: string[], live_rest?, live_ws?, testnet_rest?, testnet_ws?, user_stream_path?, market_stream_path?, confirm_live?: "CANLI"}` | `SettingsView` |
| POST | `/api/settings/connection/test` | | `{ok: boolean, message: string, equity?: number}` |
| PUT | `/api/settings/telegram` | `{enabled?, chat_id?, bot_token?, clear_token?: boolean, notify_signals?, notify_trades?}` | `SettingsView` |
| POST | `/api/settings/telegram/test` | | `{ok: boolean, message: string}` |
| GET | `/api/settings/telegram/chats` | | `{chat_id: string, title: string}[]` |
| PUT | `/api/settings/risk` | `Partial<RiskSettings>` (veya `{profile: "temkinli"\|"dengeli"\|"agresif"}`) | `SettingsView` |
| PUT | `/api/settings/scanner` | `Partial<ScannerSettings>` | `SettingsView` |
| PUT | `/api/settings/strategies` | `Partial<StrategySettings>` | `SettingsView` |
| PUT | `/api/settings/paper` | `{starting_balance?: number, reset?: boolean}` | `SettingsView` |
| POST | `/api/settings/password` | `{current, new}` | `{ok: true}` |

**Gizli alanlar** (API anahtarı, secret, özel anahtar, Telegram token) **asla geri gönderilmez.** Sadece `*_set: boolean` ve `*_hint` döner. Hint son 4 karakterdir, ör. `"••••a1b2"`.

`live` ortamına geçiş `confirm_live: "CANLI"` ister. Ortam her değiştiğinde otomatik mod güvenlik için **kapatılır**.

```ts
interface SettingsView {
  connection: {
    env: Env; key_type: "hmac" | "ed25519";
    api_key_set: boolean; api_key_hint: string;
    api_secret_set: boolean; private_key_set: boolean;
    live_rest: string; live_ws: string; testnet_rest: string; testnet_ws: string;
    user_stream_path: string; market_stream_path: string;
  };
  telegram: {
    enabled: boolean; chat_id: string; bot_token_set: boolean; bot_token_hint: string;
    notify_signals: boolean; notify_trades: boolean; daily_report: boolean;
  };
  risk: RiskSettings;
  scanner: ScannerSettings;
  strategies: StrategySettings;
  paper: { starting_balance: number; slippage_bps: number; taker_fee: number; maker_fee: number };
  profiles: Record<"temkinli" | "dengeli" | "agresif", Partial<RiskSettings>>;
}

interface RiskSettings {
  profile: "temkinli" | "dengeli" | "agresif" | "ozel";
  base_risk_pct: number; min_risk_pct: number; max_risk_pct: number;
  liq_buffer_k: number; max_margin_per_trade_pct: number;
  cap_major: number; cap_large: number; cap_other: number; global_max_leverage: number;
  max_positions: number; max_same_side: number; capital_tiers: boolean;
  max_portfolio_heat_pct: number; max_total_margin_pct: number; max_effective_leverage: number;
  max_cluster_risk_pct: number; cluster_corr: number;
  daily_loss_pct: number; weekly_loss_pct: number; max_drawdown_pct: number;
  loss_streak_count: number; loss_streak_cooldown_min: number;
  min_ev_r: number; max_entry_drift_r: number;
}

interface ScannerSettings {
  min_quote_volume: number; min_age_days: number; max_symbols: number;
  max_spread_bps: number; blacklist: string[]; signal_ttl_min: number;
}

interface StrategySettings {
  s1_enabled: boolean; s2_enabled: boolean; s3_enabled: boolean;   // S3 varsayılan kapalı
  s1_donchian: number; s1_vol_z: number; s1_atr_mult: number; s2_top_n: number;
  s3_bb_k: number;            // Bollinger genişliği (2.0-3.5)
  s3_rsi: number;             // aşırılık eşiği (15-35); long için RSI < eşik, short için > 100 − eşik
  oi_confirmation: boolean;   // fırsat puanına open interest teyidi
  health_monitor: boolean;    // performansı bozulan stratejiyi otomatik kapat
  health_min_trades: number;  // değerlendirme için en az işlem (10-100)
  health_min_expectancy_r: number;   // bu beklentinin altına düşerse kapat (ör. -0.15)
}
```

`PUT /api/settings/telegram` gövdesi `daily_report?: boolean` da kabul eder (UTC 00:05'te günlük özet).

## WebSocket `/ws`

Çerezle doğrulanır. Mesajların biçimi `{"type": string, "data": any}`.

**Sunucu → istemci:**

| type | data | Sıklık |
|---|---|---|
| `status` | `Status` | 2 sn |
| `positions` | `Position[]` (açık pozisyonlar, anlık alanlarla) | 1 sn |
| `signal` | `Signal` | yeni/güncellenen |
| `position` | `Position` | açıldı/güncellendi/kapandı |
| `event` | `EventItem` | olay oldukça |
| `kline` | `{symbol, interval, candle: Candle}` | abone olunan grafik için |
| `toast` | `{level, title, message}` | önemli bildirimler |
| `backtest` | `BacktestJob` (`result` olmadan) | backtest ilerledikçe/bitince |

**İstemci → sunucu:**

```json
{"type": "chart.sub",   "symbol": "BTCUSDT", "interval": "15m"}
{"type": "chart.unsub", "symbol": "BTCUSDT", "interval": "15m"}
```
