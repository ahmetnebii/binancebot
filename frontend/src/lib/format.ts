// Türkçe (tr-TR) sayı/zaman biçimlendirme yardımcıları.

const nfCache = new Map<string, Intl.NumberFormat>();
function nf(min: number, max: number, extra: Intl.NumberFormatOptions = {}): Intl.NumberFormat {
  const key = `${min}|${max}|${JSON.stringify(extra)}`;
  let f = nfCache.get(key);
  if (!f) {
    f = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: min, maximumFractionDigits: max, ...extra });
    nfCache.set(key, f);
  }
  return f;
}

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);

/** Fiyatın büyüklüğüne göre ondalık hane sayısı. */
export function priceDecimals(p: number | null | undefined): number {
  if (!isNum(p) || p === 0) return 2;
  const a = Math.abs(p);
  if (a >= 1000) return 2;
  if (a >= 10) return 3;
  if (a >= 1) return 4;
  if (a >= 0.1) return 5;
  if (a >= 0.01) return 6;
  if (a >= 0.0001) return 7;
  return 8;
}

export function fmtPrice(p: number | null | undefined, decimals?: number): string {
  if (!isNum(p)) return '—';
  const d = decimals ?? priceDecimals(p);
  return nf(d, d).format(p);
}

export function fmtNum(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return '—';
  return nf(digits, digits).format(x);
}

export function fmtUsd(x: number | null | undefined, opts: { sign?: boolean; digits?: number } = {}): string {
  if (!isNum(x)) return '—';
  const d = opts.digits ?? 2;
  return nf(d, d, opts.sign ? { signDisplay: 'exceptZero' } : {}).format(x);
}

/** x bir oran (0.0123 = %1,23). */
export function fmtPct(x: number | null | undefined, opts: { sign?: boolean; digits?: number } = {}): string {
  if (!isNum(x)) return '—';
  const d = opts.digits ?? 2;
  return nf(d, d, { style: 'percent', ...(opts.sign ? { signDisplay: 'exceptZero' } : {}) }).format(x);
}

export function fmtR(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return '—';
  return `${nf(digits, digits, { signDisplay: 'exceptZero' }).format(x)}R`;
}

export function fmtQty(q: number | null | undefined): string {
  if (!isNum(q)) return '—';
  const a = Math.abs(q);
  const d = a >= 1000 ? 1 : a >= 1 ? 3 : 6;
  return nf(0, d).format(q);
}

export function fmtCompact(x: number | null | undefined): string {
  if (!isNum(x)) return '—';
  return new Intl.NumberFormat('tr-TR', { notation: 'compact', maximumFractionDigits: 1 }).format(x);
}

export function fmtLev(l: number | null | undefined): string {
  if (!isNum(l)) return '—';
  return `${l}x`;
}

const dtfTime = new Intl.DateTimeFormat('tr-TR', { hour: '2-digit', minute: '2-digit' });
const dtfTimeSec = new Intl.DateTimeFormat('tr-TR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const dtfDateTime = new Intl.DateTimeFormat('tr-TR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const dtfDate = new Intl.DateTimeFormat('tr-TR', { day: 'numeric', month: 'short', year: 'numeric' });

export const fmtTime = (ms: number | null | undefined) => (isNum(ms) ? dtfTime.format(ms) : '—');
export const fmtTimeSec = (ms: number | null | undefined) => (isNum(ms) ? dtfTimeSec.format(ms) : '—');
export const fmtDateTime = (ms: number | null | undefined) => (isNum(ms) ? dtfDateTime.format(ms) : '—');
export const fmtDate = (ms: number | null | undefined) => (isNum(ms) ? dtfDate.format(ms) : '—');

/** Süre: "2 sa 14 dk", "3 g 4 sa", "45 sn". */
export function fmtDuration(ms: number | null | undefined): string {
  if (!isNum(ms)) return '—';
  const s = Math.max(0, Math.round(ms / 1000));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d} g ${h} sa`;
  if (h > 0) return `${h} sa ${m} dk`;
  if (m > 0) return `${m} dk`;
  return `${s} sn`;
}

export function fmtRelative(ms: number | null | undefined, now = Date.now()): string {
  if (!isNum(ms)) return '—';
  const diff = now - ms;
  const future = diff < 0;
  const s = Math.abs(diff) / 1000;
  let txt: string;
  if (s < 5) return 'şimdi';
  if (s < 60) txt = `${Math.floor(s)} sn`;
  else if (s < 3600) txt = `${Math.floor(s / 60)} dk`;
  else if (s < 86400) txt = `${Math.floor(s / 3600)} sa`;
  else txt = `${Math.floor(s / 86400)} g`;
  return future ? `${txt} sonra` : `${txt} önce`;
}

/** Geri sayım: "04:59" ya da "1 sa 02 dk". */
export function fmtCountdown(msLeft: number): string {
  if (msLeft <= 0) return 'doldu';
  const s = Math.floor(msLeft / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h} sa ${String(m).padStart(2, '0')} dk`;
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

const TF_SEC: Record<string, number> = { '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400 };
export const tfSeconds = (tf: string) => TF_SEC[tf] ?? 900;

export function tfLabel(tf: string): string {
  const m = /^(\d+)([mhd])$/.exec(tf);
  if (!m) return tf;
  const unit = m[2] === 'm' ? 'dk' : m[2] === 'h' ? 'sa' : 'g';
  return `${m[1]} ${unit}`;
}

export function barsToDuration(bars: number | null | undefined, tf: string): string {
  if (!isNum(bars)) return '—';
  return fmtDuration(bars * tfSeconds(tf) * 1000);
}

export const sideSign = (side: 'LONG' | 'SHORT') => (side === 'LONG' ? 1 : -1);

/** Fiyata göre yüzde uzaklık (oran olarak). */
export function distPct(from: number | null | undefined, to: number | null | undefined): number | null {
  if (!isNum(from) || !isNum(to) || from === 0) return null;
  return (to - from) / from;
}

export const pnlClass = (x: number | null | undefined) =>
  !isNum(x) || Math.abs(x) < 1e-12 ? 'text-fg' : x > 0 ? 'text-up' : 'text-down';
