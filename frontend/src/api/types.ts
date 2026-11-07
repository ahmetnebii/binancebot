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

/** R aralığı [from, to) içindeki işlem sayısı. */
export interface RBin {
  from: number;
  to: number;
  count: number;
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
  /** Eski sunucularda olmayabilir. */
  r_histogram?: RBin[];
  monthly?: { month: string; pnl: number; trades: number }[];
}

/* ------------------------------------------------------------------ */
/* Sembol analizi                                                       */
/* ------------------------------------------------------------------ */

export type TrendDir = 'up' | 'down' | 'flat';

export interface TfAnalysis {
  tf: '15m' | '1h' | '4h';
  trend: TrendDir;
  ema_stack: string;
  rsi: number | null;
  adx: number | null;
  /** yüzde (ATR / fiyat × 100) */
  atr_pct: number | null;
  /** 0 = alt bant, 1 = üst bant */
  bb_pctb: number | null;
  dist_ema20_atr: number | null;
  vol_z: number | null;
  /** yüzde, son 24 barlık değişim */
  change_pct: number | null;
}

export interface SymbolAnalysis {
  symbol: string;
  price: number | null;
  updated_at: number;
  timeframes: TfAnalysis[];
  confluence: { long: number; short: number; label: string };
  derivatives: {
    /** kesir (0.0001 = %0,01 / 8 saat) */
    funding_rate: number | null;
    next_funding_time: number | null;
    /** yüzde */
    oi_change_1h_pct: number | null;
    liq_long_1h: number | null;
    liq_short_1h: number | null;
  };
  market: {
    /** yüzde */
    change_24h_pct: number | null;
    quote_volume_24h: number | null;
    volume_rank: number | null;
    btc_corr: number | null;
    btc_beta: number | null;
    /** yüzde */
    rs_24h_pct: number | null;
  };
  liquidity: { bid: number | null; ask: number | null; spread_bps: number | null };
  regime: Regime;
  notes: string[];
}

/* ------------------------------------------------------------------ */
/* Backtest                                                             */
/* ------------------------------------------------------------------ */

export interface BacktestConfigIn {
  days?: number;
  top_n?: number;
  symbols?: string[];
  strategies?: { s1?: boolean; s2?: boolean; s3?: boolean; s4?: boolean; s5?: boolean };
  initial_equity?: number;
  slippage_bps?: number;
  taker_fee?: number;
  maker_fee?: number;
}

export interface BacktestConfig {
  days: number;
  top_n: number;
  symbols: string[];
  strategies: { s1?: boolean; s2?: boolean; s3?: boolean; s4?: boolean; s5?: boolean };
  initial_equity: number;
  slippage_bps: number;
  taker_fee: number;
  maker_fee: number;
}

export interface BacktestSummary {
  trades: number;
  /** kesir */
  win_rate: number | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  /** yüzde */
  total_return_pct: number;
  /** yüzde */
  max_drawdown_pct: number;
  sharpe: number | null;
  period_start: number;
  period_end: number;
  symbols: number;
}

export type BacktestStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
export type BacktestPhase = 'data' | 'simulate' | 'analyze' | 'done';

export interface CalibrationStats {
  n: number;
  win_rate: number;
  avg_win_r: number;
  avg_loss_r: number;
  expectancy_r: number;
}

export interface BacktestStrategyRow {
  strategy: string;
  strategy_name: string;
  trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  total_pnl: number;
  avg_win_r: number | null;
  avg_loss_r: number | null;
}

export interface BacktestTrade {
  symbol: string;
  side: Side;
  strategy: string;
  entry_time: number;
  exit_time: number;
  entry_price: number;
  exit_price: number;
  r: number;
  pnl: number;
  fees: number;
  reason: string;
  hold_bars: number;
}

export interface BacktestResult {
  summary: BacktestSummary & {
    cagr_pct: number | null;
    sortino: number | null;
    calmar: number | null;
    avg_win_r: number | null;
    avg_loss_r: number | null;
    best_r: number | null;
    worst_r: number | null;
    avg_hold_hours: number | null;
    /** yüzde */
    exposure_pct: number;
    trades_per_day: number;
    fees_total: number;
    funding_total: number;
    initial_equity: number;
    final_equity: number;
  };
  by_strategy: BacktestStrategyRow[];
  by_side: { side: Side; trades: number; win_rate: number | null; expectancy_r: number | null; total_pnl: number }[];
  /** time saniye; drawdown_pct yüzde (negatif ya da pozitif gelebilir) */
  equity: { time: number; equity: number; drawdown_pct: number }[];
  monthly: { month: string; return_pct: number }[];
  r_histogram: RBin[];
  monte_carlo: {
    runs: number;
    dd_p50: number;
    dd_p95: number;
    dd_p99: number;
    final_p5: number;
    final_p50: number;
    final_p95: number;
    /** kesir */
    prob_dd_20: number;
    prob_dd_30: number;
  } | null;
  walk_forward: {
    fold: number;
    start: number;
    end: number;
    trades: number;
    expectancy_r: number | null;
    profit_factor: number | null;
    return_pct: number;
  }[];
  trades: BacktestTrade[];
  calibration: Record<string, CalibrationStats>;
  data_source: 'binance' | 'synthetic';
  warnings: string[];
}

export interface BacktestJob {
  id: string;
  kind?: 'backtest' | 'optimize' | 'lab' | 'data' | 'carry' | 'full';
  created_at: number;
  finished_at: number | null;
  status: BacktestStatus;
  progress: { phase: BacktestPhase; pct: number; message: string };
  config: BacktestConfig;
  error: string | null;
  summary: BacktestSummary | null;
  result?: BacktestResult;
}

export interface Calibration {
  source_job: string | null;
  applied_at: number | null;
  strategies: Record<string, CalibrationStats>;
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

export type StrategySides = 'both' | 'long' | 'short';

export interface StrategySettings {
  s1_enabled: boolean;
  s2_enabled: boolean;
  s1_donchian: number;
  s1_vol_z: number;
  s1_atr_mult: number;
  s2_top_n: number;
  // Tur 2 eklemeleri (eski sunucularda olmayabilir)
  s3_enabled?: boolean;
  s1_sides?: StrategySides;
  s2_sides?: StrategySides;
  s3_sides?: StrategySides;
  s4_enabled?: boolean;
  s4_donchian?: number;
  s4_atr_mult?: number;
  s4_sides?: StrategySides;
  s5_enabled?: boolean;
  s5_pivot?: number;
  s5_tol?: number;
  s5_vol_z?: number;
  s5_sides?: StrategySides;
  lab_enabled?: boolean;
  lab_rule?: Record<string, unknown> | null;
  lab_paper_only?: boolean;
  lab_groups?: { key: string; symbols: string[]; label?: string; paper_only?: boolean; rule: Record<string, unknown> }[] | null;
  regime_filter?: 'normal' | 'strict';
  funding_max?: number;
  exit_profile?: 'standard' | 'runner' | 'quick';
  s3_bb_k?: number;
  s3_rsi?: number;
  oi_confirmation?: boolean;
  health_monitor?: boolean;
  health_min_trades?: number;
  health_min_expectancy_r?: number;
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
    daily_report?: boolean;
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
  | { type: 'toast'; data: ToastMsg }
  | { type: 'backtest'; data: BacktestJob };


// --- Otomatik optimizasyon --------------------------------------------------
export interface OptimizeEval {
  trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  t_stat: number;
  max_drawdown_pct: number;
  return_pct: number;
}

export interface OptimizeRow {
  rank?: number;
  label: string;
  details: string[];
  min_ev_r: number;
  params: { strategies: Record<string, unknown>; min_ev_r: number };
  is: OptimizeEval;
  oos: OptimizeEval;
}

export interface OptimizeResult {
  verdict: 'recommended' | 'weak' | 'none';
  verdict_text: string;
  period: { start: number; split: number; end: number };
  candidates_tested: number;
  min_trades: number;
  best: OptimizeRow | null;
  baseline: OptimizeRow | null;
  leaderboard: OptimizeRow[];
  full: BacktestResult | null;
  calibration: Record<string, unknown>;
  data_source: 'binance' | 'synthetic';
  warnings: string[];
}

export interface OptimizeJob extends Omit<BacktestJob, 'result' | 'summary' | 'config' | 'kind'> {
  kind: 'optimize';
  applied_at?: number | null;
  config: { days: number; top_n: number; symbols: string[]; budget: string; candidates: number };
  summary: { verdict: OptimizeResult['verdict']; label: string | null; oos_expectancy_r: number | null; oos_profit_factor: number | null; oos_return_pct: number | null } | null;
  result?: OptimizeResult;
}

// --- Strateji Laboratuvarı -------------------------------------------------
export interface LabStats {
  trades: number;
  expectancy_r: number | null;
  profit_factor: number | null;
  win_rate: number | null;
  t_stat: number;
  score?: number;
  folds_pos?: number;
  breadth?: number;
  return_pct?: number;
  max_drawdown_pct?: number;
}

export interface LabRow {
  label: string;
  details: string[];
  rule: Record<string, unknown>;
  is: LabStats;
  oos: LabStats | null;
  dev?: LabStats;
  final?: LabStats | null;
}

export interface LabGroup {
  key: string; // 'all' | 'big' | 'mid' | 'high' | coin sembolü
  name: string;
  symbols: string[];
  verdict: 'recommended' | 'weak' | 'none';
  verdict_text: string;
  forward_ok: boolean;
  tested: number;
  luck_bar: number;
  min_trades: number;
  shortlist: number;
  passed_oos: number;
  best: LabRow | null;
  finalists: { label: string; details: string[]; dev: LabStats }[];
  leaderboard: LabRow[];
  full: BacktestResult | null;
  full_summary?: {
    trades: number;
    win_rate: number | null;
    profit_factor: number | null;
    expectancy_r: number | null;
    total_return_pct: number;
    max_drawdown_pct: number;
    initial_equity: number;
    final_equity: number;
    avg_hold_hours: number | null;
    fees_total: number;
  } | null;
}

export interface LabResult {
  verdict: 'recommended' | 'weak' | 'none';
  verdict_text: string;
  grouped?: boolean;
  mode?: 'single' | 'groups' | 'coins';
  period: { start: number; split1: number; split2: number; end: number };
  tested: number;
  groups?: LabGroup[];
  // eski (tek kurallı) sonuç biçimi
  luck_bar?: number;
  shortlist?: number;
  passed_oos?: number;
  best?: LabRow | null;
  finalists?: LabGroup['finalists'];
  leaderboard?: LabRow[];
  full?: BacktestResult | null;
  data_source: 'binance' | 'synthetic';
  warnings: string[];
}

export interface LabJob extends Omit<BacktestJob, 'result' | 'summary' | 'config' | 'kind'> {
  kind: 'lab';
  applied_at?: number | null;
  applied_mode?: 'live' | 'paper';
  config: { days: number; top_n: number; symbols: string[]; budget: string; candidates: number; grouped?: boolean; mode?: 'single' | 'groups' | 'coins' };
  summary: { verdict: LabResult['verdict']; label: string | null; tested: number } | null;
  result?: LabResult;
}

// --- Funding arbitrajı ------------------------------------------------------
export interface CarryMetrics {
  return_pct: number;
  apr_pct: number;
  max_drawdown_pct: number;
  time_in_pct: number;
  windows: number;
  worst_window_pct: number;
  positive_months_pct: number | null;
  months: number;
}

export interface CarryParams {
  k: number;
  enter_apr: number;
  exit_apr: number;
  top_n: number;
}

export interface CarryCoin {
  symbol: string;
  avg_apr_pct: number;
  positive_pct: number;
  recent_apr_pct: number | null;
  rule: { k: number; enter_apr: number; exit_apr: number };
  train: CarryMetrics;
  test: CarryMetrics;
  always_test: CarryMetrics;
}

export interface CarryResult {
  verdict: 'recommended' | 'weak' | 'none';
  verdict_text: string;
  period: { start: number; split: number; end: number };
  costs: { spot_fee: number; perp_fee: number; slippage: number; round_trip_pct: number; leverage: number; capital_efficiency: number };
  portfolio: {
    params: CarryParams;
    train: CarryMetrics;
    test: CarryMetrics;
    equity: { time: number; equity: number; drawdown_pct: number }[];
    monthly_test: { month: string; return_pct: number }[];
    now: { symbol: string; trailing_apr_pct: number; held: boolean }[];
  };
  coins: CarryCoin[];
  excluded_no_spot?: string[];
  universe?: 'binance' | 'all' | 'cross';
  coins_total?: number;
  coins_used?: number;
  spot_where?: Record<string, string[]>;
  data_source: 'binance' | 'synthetic';
  warnings: string[];
}

export interface CarryJob extends Omit<BacktestJob, 'result' | 'summary' | 'config' | 'kind'> {
  kind: 'carry';
  applied_at?: number | null;
  config: { days: number; top_n: number; symbols: string[]; spot_fee: number; leverage: number };
  summary: { verdict: CarryResult['verdict']; test_apr_pct: number; test_max_dd_pct: number; params: CarryParams } | null;
  result?: CarryResult;
}

export interface RadarRow {
  symbol: string;
  current_rate: number | null;
  current_apr_pct: number | null;
  next_funding_time: number | null;
  interval_hours: number;
  apr_7d_pct: number | null;
  apr_30d_pct: number | null;
  trailing_apr_pct: number | null;
  held: boolean;
  selected: boolean;
  spot?: boolean;
  spot_where?: string[];
}

export interface FundingRadar {
  settings: CarryParams & { leverage: number; spot_fee: number; perp_fee: number; slippage: number; source_job?: string; applied_at?: number; universe?: 'binance' | 'all' | 'cross' };
  rows: RadarRow[];
  paper: {
    started_at: number;
    windows: number;
    return_pct: number;
    apr_pct: number | null;
    holdings: string[];
    history: { t: number; ret_pct: number; holdings: string[]; funding: Record<string, number> }[];
    capital: number;
    equity: number;
  } | null;
  updated_at: number | null;
}

export interface CrowdStatus {
  rows: number;
  symbols: number;
  first: number | null;
  last: number | null;
  funding_rows: number;
  days: number;
  state: string;
  last_run: number | null;
  last_error: string | null;
}

export interface CrowdStat {
  n: number;
  mean_pct: number | null;
  t: number | null;
  up_pct: number | null;
}

export interface CrowdAnalysis {
  days: number;
  symbols: number;
  enough: boolean;
  note: string;
  results: { feature: string; name: string; high_desc: string; high: CrowdStat; low: CrowdStat; all: CrowdStat; diff_pct: number | null; diff_t: number | null; reading: string }[];
}

// --- Tek tuşla tam analiz -----------------------------------------------------
export interface FullCandidate {
  kind: 'lab' | 'carry';
  step: string;
  job_id: string;
  title: string;
  label: string;
  details: string[];
  verdict: 'recommended' | 'weak' | 'none';
  forward_ok: boolean;
  oos_return_pct: number;
  oos_days: number;
  oos_apr_pct: number;
  oos_max_dd_pct: number | null;
  oos_trades: number | null;
  oos_expectancy_r: number | null;
  full_return_pct: number | null;
  how: string;
}

export interface FullReport {
  summary: string;
  best: FullCandidate | null;
  trial: FullCandidate | null;
  candidates: FullCandidate[];
  crowd: { days: number | null; enough: boolean | null; note: string | null; strong: { name: string; reading: string; diff_t: number }[] };
  warnings: string[];
}

export interface FullStep {
  key: string;
  name: string;
  weight: number;
  job_id: string | null;
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled' | 'queued';
  summary: Record<string, unknown> | null;
  error?: string | null;
}

export interface FullJob extends Omit<BacktestJob, 'result' | 'summary' | 'config' | 'kind'> {
  kind: 'full';
  config: { steps: FullStep[] };
  summary: { best_title: string | null; best_apr_pct: number | null; candidates: number } | null;
  result?: FullReport;
}
