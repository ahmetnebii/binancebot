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
| GET | `/api/history/stats` | `{trades, wins, losses, win_rate, expectancy_r, profit_factor, total_pnl, avg_win_r, avg_loss_r, best_r, worst_r, by_strategy: {strategy, strategy_name, trades, win_rate, total_pnl, expectancy_r}[]}` |
| GET | `/api/events?limit=200` | `EventItem[]` |

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
    notify_signals: boolean; notify_trades: boolean;
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
  s1_enabled: boolean; s2_enabled: boolean;
  s1_donchian: number; s1_vol_z: number; s1_atr_mult: number; s2_top_n: number;
}
```

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

**İstemci → sunucu:**

```json
{"type": "chart.sub",   "symbol": "BTCUSDT", "interval": "15m"}
{"type": "chart.unsub", "symbol": "BTCUSDT", "interval": "15m"}
```
