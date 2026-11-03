import clsx from 'clsx';
import { useMemo } from 'react';
import { Tooltip } from '../ui/overlay';

const MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];
const MONTHS_LONG = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];

export interface MonthValue {
  /** "2026-08" */
  month: string;
  value: number;
  /** Tooltip için ek bilgi (ör. işlem sayısı). */
  extra?: string;
}

/**
 * Yıl × ay ısı haritası. `mode="pct"` için değerler yüzde (2.5 = %2,5) olup yıllık toplam bileşik
 * hesaplanır; `mode="usd"` için değerler USDT olup yıllık toplam basit toplamdır.
 */
export function MonthlyHeatmap({
  values,
  mode,
  format,
  className,
}: {
  values: MonthValue[];
  mode: 'pct' | 'usd';
  format: (v: number) => string;
  className?: string;
}) {
  const { years, maxAbs } = useMemo(() => {
    const byYear = new Map<number, (MonthValue | null)[]>();
    for (const v of values) {
      const m = /^(\d{4})-(\d{1,2})/.exec(v.month);
      if (!m) continue;
      const y = Number(m[1]);
      const mi = Number(m[2]) - 1;
      if (mi < 0 || mi > 11) continue;
      if (!byYear.has(y)) byYear.set(y, Array(12).fill(null));
      byYear.get(y)![mi] = v;
    }
    const ys = [...byYear.entries()].sort((a, b) => a[0] - b[0]);
    const maxAbs = Math.max(1e-9, ...values.map((v) => Math.abs(v.value)));
    return { years: ys, maxAbs };
  }, [values]);

  if (!years.length) {
    return <div className={clsx('py-6 text-center text-[12px] text-subtle', className)}>Aylık veri yok</div>;
  }

  const cellStyle = (v: number) => {
    const a = Math.min(1, Math.abs(v) / maxAbs);
    const alpha = 0.08 + a * 0.42;
    return { backgroundColor: v >= 0 ? `rgba(23,201,135,${alpha})` : `rgba(240,75,91,${alpha})` };
  };

  return (
    <div className={clsx('overflow-x-auto', className)}>
      <table className="w-full min-w-[640px] border-separate border-spacing-[3px] text-[11.5px]">
        <thead>
          <tr>
            <th className="label w-12 px-1 py-1 text-left font-semibold" />
            {MONTHS.map((m) => (
              <th key={m} className="label px-1 py-1 text-center font-semibold">
                {m}
              </th>
            ))}
            <th className="label px-1 py-1 text-right font-semibold">Yıl</th>
          </tr>
        </thead>
        <tbody>
          {years.map(([y, cells]) => {
            const present = cells.filter((c): c is MonthValue => c !== null);
            const total =
              mode === 'pct'
                ? (present.reduce((acc, c) => acc * (1 + c.value / 100), 1) - 1) * 100
                : present.reduce((acc, c) => acc + c.value, 0);
            return (
              <tr key={y}>
                <td className="num px-1 text-[11.5px] font-semibold text-muted">{y}</td>
                {cells.map((c, i) =>
                  c ? (
                    <td key={i} className="p-0">
                      <Tooltip
                        content={
                          <div className="num">
                            <div className="text-fg">
                              {MONTHS_LONG[i]} {y}
                            </div>
                            <div className={c.value >= 0 ? 'text-up' : 'text-down'}>{format(c.value)}</div>
                            {c.extra && <div className="text-muted">{c.extra}</div>}
                          </div>
                        }
                        className="block w-full"
                      >
                        <div
                          tabIndex={0}
                          className={clsx('num flex h-9 w-full items-center justify-center rounded-md font-medium', c.value >= 0 ? 'text-up' : 'text-down')}
                          style={cellStyle(c.value)}
                        >
                          {format(c.value)}
                        </div>
                      </Tooltip>
                    </td>
                  ) : (
                    <td key={i} className="p-0">
                      <div className="h-9 rounded-md bg-elevated/40" />
                    </td>
                  ),
                )}
                <td className={clsx('num px-1 text-right font-semibold', total >= 0 ? 'text-up' : 'text-down')}>{format(total)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
