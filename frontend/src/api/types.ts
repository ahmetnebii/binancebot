// docs/API.md ile birebir eşleşen tipler.
// Zaman: *_at, ts, server_time → milisaniye. Grafik zamanları (Candle.time, cone/time, path/time) → saniye.

export type Env = 'paper' | 'testnet' | 'live';
export type Side = 'LONG' | 'SHORT';

export interface TakeProfit {
  r: number;
  price: number;
  fraction: number;
  filled: boolean;
  filled_at: number | null;
}

export interface SizingPlan {
  ok: boolean;
  reason: string;
  notional: number;
  qty: number;
  leverage: number;
  margin: number;
  risk_usdt: number;
  risk_pct: number;
  stop_distance_pct: number;
  liq_price: number;
  liq_distance_pct: number;
  fee_estimate: number;
  notes: string[];
  multipliers: Record<string, number>;
}

export type SignalStatus = 'ACTIVE' | 'EXECUTED' | 'DISMISSED' | 'EXPIRED' | 'REJECTED' | 'FAILED';

export interface Signal {
  id: string;
  symbol: string;
  side: Side;
  strategy: string;
  strategy_name: string;
  timeframe: string;
  created_at: number;
  expires_at: number;
  bar_time: number;
  entry: number;
  stop: number;
  take_profits: TakeProfit[];
  score: number;
  ev_r: number;
  win_prob: number;
  rr: number;
  reasons: string[];
  warnings: string[];
  features: Record<string, number>;
  status: SignalStatus;
  status_note: string;
  plan: SizingPlan | null;
  position_id: string | null;
  price: number | null;
}

export interface PositionEvent {
  ts: number;
  kind: string;
  message: string;
  price: number | null;
  qty: number | null;
}

export interface Position {
  id: string;
  env: Env;
  symbol: string;
  side: Side;
  strategy: string;
  strategy_name: string;
  signal_id: string | null;
  source: 'auto' | 'manual';
  status: 'OPEN' | 'CLOSED';
  opened_at: number;
  closed_at: number | null;
  timeframe: string;
  entry_price: number;
  qty: number;
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
  mark_price: number | null;
  unrealized_pnl: number;
  pnl_total: number;
  roe_pct: number;
  r_now: number;
  liq_distance_pct: number | null;
  stop_distance_pct: number | null;
}

export interface ConePoint {
  time: number;
  up1: number;
  dn1: number;
  up2: number;
  dn2: number;
}

export interface Projection {
  timeframe: string;
  sigma_bar: number;
  horizon_bars: number;
  target_price: number | null;
  target_label: string | null;
  p_target_first: number | null;
  p_target_first_momentum: number | null;
  expected_bars: number | null;
  eta_bars_momentum: number | null;
  cone: ConePoint[];
  momentum_path: { time: number; price: number }[];
}

export interface PositionDetail {
  position: Position;
  projection: Projection | null;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Equity {
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

export interface Regime {
  name: 'BULL' | 'BEAR' | 'RANGE' | 'CHAOS' | 'UNKNOWN';
  label: string;
  risk_mult: number;
  btc_trend: string;
  breadth: number | null;
  vol_pct: number | null;
  updated_at: number | null;
}

export interface Breaker {
  code: string;
  label: string;
  active: boolean;
  detail: string;
  until: number | null;
}

export type ConnState = 'ok' | 'connecting' | 'down' | 'n/a' | 'error' | 'no_keys' | 'starting';

export interface Status {
  env: Env;
  data_source: 'binance' | 'synthetic';
  auto_mode: boolean;
  paused: boolean;
  paused_reason: string;
  connection: {
    market: 'ok' | 'connecting' | 'down';
    user_stream: 'ok' | 'connecting' | 'down' | 'n/a';
    broker: 'ok' | 'error' | 'no_keys' | 'starting';
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

export type EventLevel = 'info' | 'success' | 'warning' | 'error';

export interface EventItem {
  id: number;
  ts: number;
  level: EventLevel;
  kind: string;
  message: string;
}

export interface ManualOrder {
  symbol: string;
  side: Side;
  stop_price: number;
  take_profits?: { r?: number; price?: number; fraction: number }[];
  risk_pct?: number;
  leverage?: number;
}

export interface OrderPreview {
  plan: SizingPlan;
  entry: number;
  take_profits: TakeProfit[];
}

export interface MarketSymbol {
  symbol: string;
  price: number;
  change_pct: number;
  quote_volume: number;
  funding_rate: number | null;
  in_universe: boolean;
}

export interface EquityPoint {
  time: number;
  equity: number;
}

export interface StrategyStats {
  strategy: string;
  strategy_name: string;
  trades: number;
  win_rate: number | null;
  total_pnl: number;
  expectancy_r: number | null;
}

export interface HistoryStats {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  expectancy_r: number | null;
  profit_factor: number | null;
  total_pnl: number;
  avg_win_r: number | null;
  avg_loss_r: number | null;
  best_r: number | null;
  worst_r: number | null;
  by_strategy: StrategyStats[];
}

export type RiskProfile = 'temkinli' | 'dengeli' | 'agresif' | 'ozel';

export interface RiskSettings {
  profile: RiskProfile;
  base_risk_pct: number;
  min_risk_pct: number;
  max_risk_pct: number;
  liq_buffer_k: number;
  max_margin_per_trade_pct: number;
  cap_major: number;
  cap_large: number;
  cap_other: number;
  global_max_leverage: number;
  max_positions: number;
  max_same_side: number;
  capital_tiers: boolean;
  max_portfolio_heat_pct: number;
  max_total_margin_pct: number;
  max_effective_leverage: number;
  max_cluster_risk_pct: number;
  cluster_corr: number;
  daily_loss_pct: number;
  weekly_loss_pct: number;
  max_drawdown_pct: number;
  loss_streak_count: number;
  loss_streak_cooldown_min: number;
  min_ev_r: number;
  max_entry_drift_r: number;
}

export interface ScannerSettings {
  min_quote_volume: number;
  min_age_days: number;
  max_symbols: number;
  max_spread_bps: number;
  blacklist: string[];
  signal_ttl_min: number;
}

export interface StrategySettings {
  s1_enabled: boolean;
  s2_enabled: boolean;
  s1_donchian: number;
  s1_vol_z: number;
  s1_atr_mult: number;
  s2_top_n: number;
}

export interface PaperSettings {
  starting_balance: number;
  slippage_bps: number;
  taker_fee: number;
  maker_fee: number;
}

export interface SettingsView {
  connection: {
    env: Env;
    key_type: 'hmac' | 'ed25519';
    api_key_set: boolean;
    api_key_hint: string;
    api_secret_set: boolean;
    private_key_set: boolean;
    live_rest: string;
    live_ws: string;
    testnet_rest: string;
    testnet_ws: string;
    user_stream_path: string;
    market_stream_path: string;
  };
  telegram: {
    enabled: boolean;
    chat_id: string;
    bot_token_set: boolean;
    bot_token_hint: string;
    notify_signals: boolean;
    notify_trades: boolean;
  };
  risk: RiskSettings;
  scanner: ScannerSettings;
  strategies: StrategySettings;
  paper: PaperSettings;
  profiles: Record<'temkinli' | 'dengeli' | 'agresif', Partial<RiskSettings>>;
}

export interface ToastMsg {
  level: EventLevel;
  title: string;
  message: string;
}

export type WsMessage =
  | { type: 'status'; data: Status }
  | { type: 'positions'; data: Position[] }
  | { type: 'signal'; data: Signal }
  | { type: 'position'; data: Position }
  | { type: 'event'; data: EventItem }
  | { type: 'kline'; data: { symbol: string; interval: string; candle: Candle } }
  | { type: 'toast'; data: ToastMsg };
