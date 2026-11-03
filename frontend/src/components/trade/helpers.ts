import type { Position, Projection, Signal } from '../../api/types';
import type { ChartMarker, ChartOverlay } from '../charts/TradeChart';

export function signalOverlay(s: Signal): ChartOverlay {
  return {
    side: s.side,
    entry: s.entry,
    stop: s.stop,
    takeProfits: s.take_profits.map((t, i) => ({ price: t.price, label: `TP${i + 1}`, filled: false })),
    liq: s.plan?.ok ? s.plan.liq_price : null,
    startTime: s.bar_time || s.created_at,
    preview: true,
  };
}

const EVENT_MARKER: Record<string, ChartMarker['kind'] | undefined> = {
  open: 'entry',
  tp: 'tp',
  sl: 'sl',
  close: 'close',
  partial_close: 'partial_close',
};

export function positionOverlay(p: Position, projection: Projection | null | undefined): ChartOverlay {
  const markers: ChartMarker[] = [];
  let hasEntry = false;
  for (const e of p.events) {
    const kind = EVENT_MARKER[e.kind];
    if (!kind) continue;
    if (kind === 'entry') hasEntry = true;
    markers.push({ time: e.ts, kind, text: markerText(kind, e.message) });
  }
  if (!hasEntry) markers.push({ time: p.opened_at, kind: 'entry', text: 'Giriş' });
  const breakeven = Math.abs(p.stop_price - p.entry_price) / p.entry_price < 0.0008;
  return {
    side: p.side,
    entry: p.entry_price,
    stop: p.stop_price,
    takeProfits: p.take_profits.map((t, i) => ({ price: t.price, label: `TP${i + 1}`, filled: t.filled })),
    liq: p.liq_price,
    startTime: p.opened_at,
    endTime: p.status === 'CLOSED' ? p.closed_at : null,
    markers,
    projection: p.status === 'OPEN' ? projection ?? null : null,
    breakeven,
  };
}

function markerText(kind: ChartMarker['kind'], msg: string): string {
  switch (kind) {
    case 'entry':
      return 'Giriş';
    case 'tp': {
      const m = /TP\s?\d/i.exec(msg);
      return m ? m[0].toUpperCase().replace(' ', '') : 'TP';
    }
    case 'sl':
      return 'SL';
    case 'partial_close':
      return 'Kısmi';
    case 'close':
      return 'Kapanış';
    default:
      return '';
  }
}

export function strategyTone(code: string): 'accent' | 'proj' | 'warn' | 'neutral' {
  if (code === 'S1') return 'accent';
  if (code === 'S2') return 'proj';
  if (code === 'M') return 'neutral';
  return 'warn';
}

export const MULTIPLIER_LABELS: Record<string, string> = {
  quality: 'Kalite',
  kalite: 'Kalite',
  drawdown: 'Düşüş',
  dusus: 'Düşüş',
  streak: 'Kayıp serisi',
  seri: 'Kayıp serisi',
  regime: 'Rejim',
  rejim: 'Rejim',
  capital: 'Sermaye',
  sermaye: 'Sermaye',
  kelly: 'Kelly',
};

export const CLOSE_REASON_LABEL: Record<string, string> = {
  sl: 'Stop-loss',
  tp: 'Hedef',
  manual: 'Manuel',
  time_stop: 'Zaman stopu',
  trailing: 'İz süren stop',
  liquidation: 'Likidasyon',
  adl: 'ADL',
  close_all: 'Toplu kapanış',
};

export function closeReasonText(r: string | null | undefined): string {
  if (!r) return '—';
  return CLOSE_REASON_LABEL[r] ?? r;
}
