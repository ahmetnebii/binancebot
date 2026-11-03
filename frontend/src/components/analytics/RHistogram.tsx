import clsx from 'clsx';
import { useMemo, useState } from 'react';
import type { RBin } from '../../api/types';
import { fmtNum } from '../../lib/format';

function rLabel(x: number): string {
  if (!Number.isFinite(x)) return x > 0 ? '∞' : '−∞';
  const s = fmtNum(x, Math.abs(x) % 1 === 0 ? 0 : 1);
  return `${x > 0 ? '+' : ''}${s}R`;
}

/**
 * İşlem sonuçlarının R dağılımı. Negatif aralıklar kırmızı, pozitifler yeşil.
 * Kutucuklar (bins) sunucudan hazır gelir: [from, to).
 */
export function RHistogram({ bins, height = 170, className }: { bins: RBin[]; height?: number; className?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const data = useMemo(() => [...bins].sort((a, b) => a.from - b.from), [bins]);
  const max = Math.max(1, ...data.map((b) => b.count));
  const total = data.reduce((a, b) => a + b.count, 0);
  const zeroIdx = data.findIndex((b) => b.from >= 0);
  const labelEvery = Math.max(1, Math.ceil(data.length / 8));

  if (!data.length || total === 0) {
    return (
      <div className={clsx('flex items-center justify-center text-[12px] text-subtle', className)} style={{ height }}>
        Dağılım için yeterli işlem yok
      </div>
    );
  }

  return (
    <div className={clsx('relative select-none', className)}>
      <div className="flex">
        {/* Y ekseni */}
        <div className="num flex w-8 shrink-0 flex-col justify-between pb-0 pr-2 text-right text-[10px] text-subtle" style={{ height }}>
          <span>{max}</span>
          <span>{Math.round(max / 2)}</span>
          <span>0</span>
        </div>
        <div className="relative min-w-0 flex-1">
          {/* yatay ızgara */}
          <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-line/70" />
          <div className="pointer-events-none absolute inset-x-0 border-t border-dashed border-line/50" style={{ top: height / 2 }} />
          <div className="flex items-end gap-[3px] border-b border-line" style={{ height }} onMouseLeave={() => setHover(null)}>
            {data.map((b, i) => {
              const mid = (b.from + b.to) / 2;
              const neg = Number.isFinite(mid) ? mid < 0 : b.to <= 0;
              const h = (b.count / max) * (height - 4);
              return (
                <div
                  key={`${b.from}-${b.to}`}
                  className="relative flex h-full flex-1 items-end"
                  onMouseEnter={() => setHover(i)}
                >
                  {i === zeroIdx && zeroIdx > 0 && (
                    <div className="pointer-events-none absolute -left-[2px] bottom-0 top-0 border-l border-dashed border-fg/30" />
                  )}
                  <div
                    className={clsx(
                      'w-full rounded-t-[3px] transition-[height,opacity] duration-300',
                      neg ? 'bg-down/80' : 'bg-up/80',
                      hover !== null && hover !== i && 'opacity-45',
                    )}
                    style={{ height: Math.max(b.count > 0 ? 2 : 0, h) }}
                  />
                </div>
              );
            })}
          </div>
          {/* X etiketleri */}
          <div className="relative mt-1.5 h-4">
            {data.map((b, i) =>
              i % labelEvery === 0 ? (
                <span
                  key={`l-${b.from}`}
                  className="num absolute -translate-x-1/2 whitespace-nowrap text-[10px] text-subtle"
                  style={{ left: `${(i / data.length) * 100}%` }}
                >
                  {rLabel(b.from)}
                </span>
              ) : null,
            )}
          </div>
          {hover !== null && data[hover] && (
            <div
              className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg border border-line-strong bg-elevated px-2.5 py-1.5 text-[11.5px] shadow-pop"
              style={{ left: `${((hover + 0.5) / data.length) * 100}%` }}
            >
              <div className="num text-fg">
                {rLabel(data[hover].from)} … {rLabel(data[hover].to)}
              </div>
              <div className="num text-muted">
                {data[hover].count} işlem · %{fmtNum((data[hover].count / total) * 100, 1)}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
