// Belirsiz birimli *_pct alanları yerine mutlak değerlerden türetilen oranlar (0.01 = %1).
import type { Equity, Position } from '../api/types';

export const roeFrac = (p: Position) => (p.margin > 0 ? p.pnl_total / p.margin : 0);

export function stopDistFrac(p: Position): number | null {
  const m = p.mark_price ?? p.exit_price ?? p.entry_price;
  if (!m || !p.stop_price) return null;
  return Math.abs(m - p.stop_price) / m;
}

export function liqDistFrac(p: Position): number | null {
  const m = p.mark_price ?? p.entry_price;
  if (!m || !p.liq_price || p.liq_price <= 0) return null;
  return Math.abs(m - p.liq_price) / m;
}

export const eqMarginFrac = (e: Equity) => (e.equity > 0 ? e.margin_used / e.equity : 0);
export const eqRiskFrac = (e: Equity) => (e.equity > 0 ? e.open_risk / e.equity : 0);
export function eqDayFrac(e: Equity): number {
  const start = e.equity - e.day_pnl;
  return start > 0 ? e.day_pnl / start : 0;
}
export const eqDrawdownFrac = (e: Equity) => (e.hwm > 0 ? Math.max(0, (e.hwm - e.equity) / e.hwm) : 0);
