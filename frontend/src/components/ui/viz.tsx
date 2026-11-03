import clsx from 'clsx';
import type { ReactNode } from 'react';
import { useId, useMemo } from 'react';
import type { Position, TakeProfit } from '../../api/types';
import { fmtR, fmtUsd, fmtPct, pnlClass } from '../../lib/format';

export function ScoreRing({ score, size = 40 }: { score: number; size?: number }) {
  const s = Math.max(0, Math.min(100, score));
  const stroke = 3.5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const color = s >= 80 ? '#17C987' : s >= 65 ? '#5B8CFF' : s >= 50 ? '#F5A524' : '#8B95A5';
  return (
    <div className="relative inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(34 41 54)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${(s / 100) * c} ${c}`}
          style={{ transition: 'stroke-dasharray 600ms ease' }}
        />
      </svg>
      <span className="num absolute text-[12px] font-semibold" style={{ color }}>
        {Math.round(s)}
      </span>
    </div>
  );
}

export function Sparkline({
  values,
  height = 36,
  className,
  color,
}: {
  values: number[];
  height?: number;
  className?: string;
  color?: string;
}) {
  const gid = useId().replace(/:/g, '');
  const w = 120;
  const path = useMemo(() => {
    if (values.length < 2) return null;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const pts = values.map((v, i) => [(i / (values.length - 1)) * w, height - 3 - ((v - min) / span) * (height - 6)] as const);
    const line = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
    const area = `${line} L${w},${height} L0,${height} Z`;
    return { line, area };
  }, [values, height]);
  const up = values.length >= 2 ? values[values.length - 1] >= values[0] : true;
  const stroke = color ?? (up ? '#17C987' : '#F04B5B');
  if (!path) return <div className={clsx('h-9', className)} />;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className={clsx('w-full', className)} style={{ height }}>
      <defs>
        <linearGradient id={`sg${gid}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="1" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={path.area} fill={`url(#sg${gid})`} />
      <path d={path.line} fill="none" stroke={stroke} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/** Pozisyon için R ölçeği: risk birimi = |giriş − ilk stop|. */
export function riskUnit(p: Pick<Position, 'entry_price' | 'initial_stop'>): number {
  return Math.abs(p.entry_price - p.initial_stop) || 1e-12;
}

export function priceToR(p: Pick<Position, 'entry_price' | 'initial_stop' | 'side'>, price: number): number {
  const sign = p.side === 'LONG' ? 1 : -1;
  return ((price - p.entry_price) * sign) / riskUnit(p);
}

/**
 * SL'den TP'lere uzanan R ilerleme çubuğu.
 */
export function RProgress({
  rNow,
  stopR,
  tps,
  compact,
  className,
}: {
  rNow: number;
  stopR: number;
  tps: { r: number; filled: boolean; label: string }[];
  compact?: boolean;
  className?: string;
}) {
  const lastTp = tps.length ? Math.max(...tps.map((t) => t.r)) : 2;
  const min = Math.min(-1, stopR, rNow) - 0.15;
  const max = Math.max(lastTp, rNow, 1) + 0.15;
  const span = max - min;
  const x = (r: number) => `${((r - min) / span) * 100}%`;
  const clampR = Math.max(min, Math.min(max, rNow));
  const pos = rNow >= 0;
  return (
    <div className={clsx('w-full select-none', className)}>
      <div className={clsx('relative', compact ? 'h-5' : 'h-9')}>
        {/* Kayıp ve kazanç bölgeleri */}
        <div className={clsx('absolute inset-x-0 rounded-full bg-elevated', compact ? 'top-2 h-1.5' : 'top-[15px] h-2')} />
        <div
          className={clsx('absolute rounded-l-full', compact ? 'top-2 h-1.5' : 'top-[15px] h-2')}
          style={{ left: x(min), width: `calc(${x(0)} - ${x(min)})`, background: 'linear-gradient(90deg, rgba(240,75,91,0.35), rgba(240,75,91,0.08))' }}
        />
        <div
          className={clsx('absolute rounded-r-full', compact ? 'top-2 h-1.5' : 'top-[15px] h-2')}
          style={{ left: x(0), width: `calc(${x(max)} - ${x(0)})`, background: 'linear-gradient(90deg, rgba(23,201,135,0.08), rgba(23,201,135,0.30))' }}
        />
        {/* Mevcut ilerleme dolgusu */}
        <div
          className={clsx('absolute transition-all duration-500', compact ? 'top-2 h-1.5' : 'top-[15px] h-2', pos ? 'bg-up' : 'bg-down')}
          style={{
            left: pos ? x(0) : x(clampR),
            width: `calc(${pos ? x(clampR) : x(0)} - ${pos ? x(0) : x(clampR)})`,
            opacity: 0.85,
          }}
        />
        {/* Giriş (0R) */}
        <div className={clsx('absolute w-px bg-fg/70', compact ? 'top-1 h-3.5' : 'top-[11px] h-4')} style={{ left: x(0) }} />
        {/* Stop */}
        <div
          className={clsx('absolute -translate-x-1/2 rounded-sm bg-down', compact ? 'top-1 h-3.5 w-[3px]' : 'top-[10px] h-[18px] w-[3px]')}
          style={{ left: x(stopR) }}
          title={`Stop ${fmtR(stopR, 1)}`}
        />
        {/* TP'ler */}
        {tps.map((t) => (
          <div
            key={t.label}
            className={clsx('absolute -translate-x-1/2 rounded-sm', t.filled ? 'bg-up' : 'bg-up/60', compact ? 'top-1 h-3.5 w-[3px]' : 'top-[10px] h-[18px] w-[3px]')}
            style={{ left: x(t.r) }}
            title={`${t.label} ${fmtR(t.r, 1)}`}
          />
        ))}
        {/* Mevcut konum */}
        <div
          className={clsx(
            'absolute -translate-x-1/2 rounded-full border-2 border-bg shadow transition-all duration-500',
            pos ? 'bg-up' : 'bg-down',
            compact ? 'top-[3px] h-3.5 w-3.5' : 'top-[11px] h-4 w-4',
          )}
          style={{ left: x(clampR) }}
        />
        {!compact && (
          <div
            className={clsx('num absolute top-[-6px] -translate-x-1/2 whitespace-nowrap text-[11px] font-semibold transition-all duration-500', pos ? 'text-up' : 'text-down')}
            style={{ left: x(clampR) }}
          >
            {fmtR(rNow)}
          </div>
        )}
      </div>
      {!compact && (
        <div className="relative mt-1 h-4 text-[10.5px] text-subtle">
          <span className="num absolute -translate-x-1/2 whitespace-nowrap text-down/90" style={{ left: x(stopR) }}>
            SL
          </span>
          <span className="num absolute -translate-x-1/2 whitespace-nowrap" style={{ left: x(0) }}>
            Giriş
          </span>
          {tps.map((t) => (
            <span key={t.label} className={clsx('num absolute -translate-x-1/2 whitespace-nowrap', t.filled ? 'text-up' : 'text-up/80')} style={{ left: x(t.r) }}>
              {t.label}
              {t.filled ? ' ✓' : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function positionRBar(p: Position, compact?: boolean, className?: string) {
  const stopR = priceToR(p, p.stop_price);
  const tps = p.take_profits.map((t: TakeProfit, i: number) => ({
    r: Number.isFinite(t.r) && t.r !== 0 ? t.r : priceToR(p, t.price),
    filled: t.filled,
    label: `TP${i + 1}`,
  }));
  return <RProgress rNow={p.r_now} stopR={stopR} tps={tps} compact={compact} className={className} />;
}

export function Pnl({
  value,
  pct,
  size = 'md',
  className,
}: {
  value: number | null | undefined;
  pct?: number | null;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}) {
  const sz = { sm: 'text-[12.5px]', md: 'text-[13.5px]', lg: 'text-[18px]', xl: 'text-[28px] tracking-tight' }[size];
  return (
    <span className={clsx('num inline-flex items-baseline gap-1.5 font-semibold', sz, pnlClass(value), className)}>
      {fmtUsd(value, { sign: true })}
      {size === 'xl' && <span className="text-[13px] font-medium text-muted">USDT</span>}
      {pct !== undefined && pct !== null && (
        <span className={clsx('font-medium', size === 'xl' ? 'text-[15px]' : 'text-[0.85em]', pnlClass(pct))}>({fmtPct(pct, { sign: true })})</span>
      )}
    </span>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  items,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: ReactNode; count?: number; icon?: ReactNode }[];
  className?: string;
}) {
  return (
    <div className={clsx('flex items-center gap-1 overflow-x-auto border-b border-line', className)}>
      {items.map((it) => (
        <button
          key={it.value}
          type="button"
          onClick={() => onChange(it.value)}
          className={clsx(
            'relative inline-flex h-10 shrink-0 items-center gap-2 px-3 text-[13px] font-medium transition-colors',
            it.value === value ? 'text-fg' : 'text-muted hover:text-fg',
          )}
        >
          {it.icon}
          {it.label}
          {it.count !== undefined && (
            <span
              className={clsx(
                'num rounded-md px-1.5 text-[10.5px] font-semibold',
                it.value === value ? 'bg-accent/15 text-accent' : 'bg-elevated text-subtle',
              )}
            >
              {it.count}
            </span>
          )}
          {it.value === value && <span className="absolute inset-x-2 -bottom-px h-[2px] rounded-full bg-accent" />}
        </button>
      ))}
    </div>
  );
}
