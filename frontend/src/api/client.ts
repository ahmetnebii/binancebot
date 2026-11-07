import { toast, useApp } from '../store/app';
import type {
  BacktestConfigIn,
  BacktestJob,
  OptimizeJob,
  FullJob,
  CarryJob,
  FundingRadar,
  CrowdStatus,
  CrowdAnalysis,
  LabJob,
  Calibration,
  Candle,
  EquityPoint,
  EventItem,
  HistoryStats,
  ManualOrder,
  MarketSymbol,
  OrderPreview,
  Position,
  PositionDetail,
  RiskSettings,
  ScannerSettings,
  SettingsView,
  Signal,
  Status,
  StrategySettings,
  SymbolAnalysis,
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailToText(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    // FastAPI doğrulama hatası: [{loc, msg, type}]
    const parts = detail
      .map((d) => {
        if (d && typeof d === 'object') {
          const o = d as { loc?: unknown[]; msg?: string };
          const field = Array.isArray(o.loc) ? o.loc.filter((x) => x !== 'body').join('.') : '';
          return field ? `${field}: ${o.msg ?? ''}` : (o.msg ?? '');
        }
        return String(d);
      })
      .filter(Boolean);
    if (parts.length) return `Geçersiz değer — ${parts.join('; ')}`;
  }
  if (status === 0) return 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edin.';
  if (status >= 500) return `Sunucu hatası (${status}). Günlük sayfasını kontrol edin.`;
  return `İstek başarısız (${status})`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-BB-Client'] = 'ui';
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, detailToText(null, 0));
  }
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    const st = useApp.getState();
    if (st.auth === 'ok') st.setAuth('login');
    throw new ApiError(401, 'Oturumunuz sona erdi, lütfen tekrar giriş yapın.');
  }
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? (data as { detail?: unknown }).detail : data;
    throw new ApiError(res.status, detailToText(detail, res.status));
  }
  return data as T;
}

/** Hata olursa toast gösterir ve hatayı yeniden fırlatır. */
export async function withToast<T>(p: Promise<T>, title = 'İşlem başarısız'): Promise<T> {
  try {
    return await p;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (!(e instanceof ApiError && e.status === 401)) toast.error(title, msg);
    throw e;
  }
}

export const api = {
  // Kimlik doğrulama
  authState: () => request<{ setup_required: boolean; authenticated: boolean }>('GET', '/api/auth/state'),
  setup: (password: string) => request<{ ok: boolean }>('POST', '/api/auth/setup', { password }),
  login: (password: string) => request<{ ok: boolean }>('POST', '/api/auth/login', { password }),
  logout: () => request<{ ok: boolean }>('POST', '/api/auth/logout'),

  // Durum ve kontrol
  status: () => request<Status>('GET', '/api/status'),
  setAuto: (enabled: boolean) => request<Status>('POST', '/api/control/auto', { enabled }),
  setPaused: (paused: boolean, reason?: string) => request<Status>('POST', '/api/control/pause', { paused, reason }),
  closeAll: () => request<{ closed: number }>('POST', '/api/control/close-all'),

  // Fırsatlar
  signals: (scope: 'active' | 'recent') => request<Signal[]>('GET', `/api/signals?scope=${scope}`),
  executeSignal: (id: string, body: { risk_pct?: number; leverage?: number }) =>
    request<Position>('POST', `/api/signals/${encodeURIComponent(id)}/execute`, body),
  dismissSignal: (id: string) => request<Signal>('POST', `/api/signals/${encodeURIComponent(id)}/dismiss`),
  /** Fırsat için canlı plan: /execute ile aynı kurallar (rejim çarpanı, giriş kayması koruması). */
  signalPreview: (id: string, body: { risk_pct?: number; leverage?: number }) =>
    request<OrderPreview>('POST', `/api/signals/${encodeURIComponent(id)}/preview`, body),

  // Pozisyonlar
  positions: (status: 'open' | 'closed', limit = 100) =>
    request<Position[]>('GET', `/api/positions?status=${status}&limit=${limit}`),
  position: (id: string) => request<PositionDetail>('GET', `/api/positions/${encodeURIComponent(id)}`),
  closePosition: (id: string, fraction: number) =>
    request<Position>('POST', `/api/positions/${encodeURIComponent(id)}/close`, { fraction }),
  moveStop: (id: string, price: number) =>
    request<Position>('POST', `/api/positions/${encodeURIComponent(id)}/stop`, { price }),
  breakeven: (id: string) => request<Position>('POST', `/api/positions/${encodeURIComponent(id)}/breakeven`),
  setTakeProfits: (id: string, take_profits: { price: number; fraction: number }[]) =>
    request<Position>('POST', `/api/positions/${encodeURIComponent(id)}/take-profits`, { take_profits }),

  // Manuel emir
  preview: (o: ManualOrder) => request<OrderPreview>('POST', '/api/orders/preview', o),
  manualOrder: (o: ManualOrder) => request<Position>('POST', '/api/orders/manual', o),

  // Piyasa / geçmiş
  symbols: () => request<MarketSymbol[]>('GET', '/api/market/symbols'),
  klines: (symbol: string, interval: string, limit = 300) =>
    request<Candle[]>(
      'GET',
      `/api/market/klines?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}&limit=${limit}`,
    ),
  equity: (range: '1d' | '7d' | '30d' | 'all') => request<EquityPoint[]>('GET', `/api/history/equity?range=${range}`),
  stats: () => request<HistoryStats>('GET', '/api/history/stats'),
  events: (limit = 200) => request<EventItem[]>('GET', `/api/events?limit=${limit}`),
  analysis: (symbol: string) => request<SymbolAnalysis>('GET', `/api/market/analysis?symbol=${encodeURIComponent(symbol)}`),

  // Backtest
  runBacktest: (cfg: BacktestConfigIn) => request<BacktestJob>('POST', '/api/backtest/run', cfg),
  backtestJobs: () => request<BacktestJob[]>('GET', '/api/backtest/jobs'),
  backtestJob: (id: string) => request<BacktestJob>('GET', `/api/backtest/jobs/${encodeURIComponent(id)}`),
  cancelBacktest: (id: string) => request<BacktestJob>('POST', `/api/backtest/jobs/${encodeURIComponent(id)}/cancel`),
  deleteBacktest: (id: string) => request<{ ok: boolean }>('DELETE', `/api/backtest/jobs/${encodeURIComponent(id)}`),
  applyCalibration: (id: string) => request<Calibration>('POST', `/api/backtest/jobs/${encodeURIComponent(id)}/apply-calibration`),
  calibration: () => request<Calibration>('GET', '/api/backtest/calibration'),
  runOptimize: (cfg: { days?: number; top_n?: number; budget?: string }) => request<OptimizeJob>('POST', '/api/backtest/optimize', cfg),
  optimizeJobs: () => request<OptimizeJob[]>('GET', '/api/backtest/optimize/jobs'),
  optimizeJob: (id: string) => request<OptimizeJob>('GET', `/api/backtest/jobs/${encodeURIComponent(id)}`),
  runFull: () => request<FullJob>('POST', '/api/backtest/full'),
  fullJobs: () => request<FullJob[]>('GET', '/api/backtest/full/jobs'),
  fullJob: (id: string) => request<FullJob>('GET', `/api/backtest/jobs/${encodeURIComponent(id)}`),
  runCarry: (cfg: { days?: number; top_n?: number; spot_fee?: number; leverage?: number; universe?: 'binance' | 'all' | 'cross' }) => request<CarryJob>('POST', '/api/backtest/carry', cfg),
  carryJobs: () => request<CarryJob[]>('GET', '/api/backtest/carry/jobs'),
  carryJob: (id: string) => request<CarryJob>('GET', `/api/backtest/jobs/${encodeURIComponent(id)}`),
  applyCarry: (id: string) => request<{ ok: boolean }>('POST', `/api/backtest/carry/${encodeURIComponent(id)}/apply`),
  fundingRadar: () => request<FundingRadar>('GET', '/api/crowd/funding-radar'),
  crowdStatus: () => request<CrowdStatus>('GET', '/api/crowd/status'),
  crowdAnalysis: () => request<CrowdAnalysis>('GET', '/api/crowd/analysis'),
  crowdCollect: () => request<{ ok: boolean }>('POST', '/api/crowd/collect'),
  paperReset: () => request<{ ok: boolean }>('POST', '/api/crowd/paper/reset'),
  dataStatus: () =>
    request<{ symbols: number; first: number | null; last: number | null; updated_at: number | null; size_mb: number; synthetic?: boolean; running_job?: string }>(
      'GET',
      '/api/backtest/data/status',
    ),
  dataSync: (cfg: { days?: number; top_n?: number }) => request<BacktestJob>('POST', '/api/backtest/data/sync', cfg),
  runLab: (cfg: { days?: number; top_n?: number; budget?: string; grouped?: boolean; mode?: 'single' | 'groups' | 'coins' }) => request<LabJob>('POST', '/api/backtest/lab', cfg),
  labJobs: () => request<LabJob[]>('GET', '/api/backtest/lab/jobs'),
  labJob: (id: string) => request<LabJob>('GET', `/api/backtest/jobs/${encodeURIComponent(id)}`),
  applyLab: (id: string, mode: 'live' | 'paper') =>
    request<{ ok: boolean; mode: string; groups: string[]; calibrated: boolean }>('POST', `/api/backtest/lab/${encodeURIComponent(id)}/apply`, { mode }),
  applyOptimize: (id: string) => request<{ ok: boolean; calibrated: boolean }>('POST', `/api/backtest/optimize/${encodeURIComponent(id)}/apply`),
  resetCalibration: () => request<Calibration>('DELETE', '/api/backtest/calibration'),

  // Ayarlar
  settings: () => request<SettingsView>('GET', '/api/settings'),
  saveConnection: (body: Record<string, unknown>) => request<SettingsView>('PUT', '/api/settings/connection', body),
  testConnection: () => request<{ ok: boolean; message: string; equity?: number }>('POST', '/api/settings/connection/test'),
  saveTelegram: (body: Record<string, unknown>) => request<SettingsView>('PUT', '/api/settings/telegram', body),
  testTelegram: () => request<{ ok: boolean; message: string }>('POST', '/api/settings/telegram/test'),
  telegramChats: () => request<{ chat_id: string; title: string }[]>('GET', '/api/settings/telegram/chats'),
  saveRisk: (body: Partial<RiskSettings> | { profile: string }) => request<SettingsView>('PUT', '/api/settings/risk', body),
  saveScanner: (body: Partial<ScannerSettings>) => request<SettingsView>('PUT', '/api/settings/scanner', body),
  saveStrategies: (body: Partial<StrategySettings>) => request<SettingsView>('PUT', '/api/settings/strategies', body),
  savePaper: (body: { starting_balance?: number; reset?: boolean }) =>
    request<SettingsView>('PUT', '/api/settings/paper', body),
  changePassword: (current: string, next: string) =>
    request<{ ok: boolean }>('POST', '/api/settings/password', { current, new: next }),
};
