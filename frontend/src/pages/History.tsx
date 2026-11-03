import clsx from 'clsx';
import { BarChart3, History as HistoryIcon } from 'lucide-react';
import { api } from '../api/client';
import type { HistoryStats, Position } from '../api/types';
import { ClosedPositionsTable } from '../components/trade/PositionViews';
import { Card, CardHeader, Empty, Skeleton } from '../components/ui/primitives';
import { useAsync } from '../lib/hooks';
import { fmtNum, fmtPct, fmtR, fmtUsd, pnlClass } from '../lib/format';

function StatTile({ label, value, cls, sub }: { label: string; value: string; cls?: string; sub?: string }) {
  return (
    <div className="min-w-0 px-4 py-3.5">
      <div className="label">{label}</div>
      <div className={clsx('num mt-1 truncate text-[19px] font-semibold tracking-tight text-fg', cls)}>{value}</div>
      {sub && <div className="num mt-0.5 text-[11px] text-subtle">{sub}</div>}
    </div>
  );
}

export function History() {
  const stats = useAsync<HistoryStats>(() => api.stats(), []);
  const closed = useAsync<Position[]>(() => api.positions('closed', 300), []);
  const s = stats.data;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[20px] font-semibold tracking-tight text-fg">Geçmiş</h1>
        <p className="mt-0.5 text-[12.5px] text-muted">Kapanan işlemlerin performansı. Bir işleme tıklayarak grafikte giriş/çıkış noktalarını görebilirsin.</p>
      </div>

      <Card className="overflow-hidden">
        {s ? (
          <div className="grid grid-cols-2 divide-x divide-y divide-line sm:grid-cols-4 xl:grid-cols-8 xl:divide-y-0">
            <StatTile label="İşlem" value={String(s.trades)} sub={`${s.wins} kazanç · ${s.losses} kayıp`} />
            <StatTile label="Kazanma oranı" value={s.win_rate != null ? fmtPct(s.win_rate, { digits: 1 }) : '—'} />
            <StatTile label="Profit factor" value={s.profit_factor != null ? fmtNum(s.profit_factor, 2) : '—'} cls={s.profit_factor != null && s.profit_factor < 1 ? 'text-down' : undefined} />
            <StatTile label="Beklenti" value={fmtR(s.expectancy_r)} cls={pnlClass(s.expectancy_r)} sub="işlem başına" />
            <StatTile label="Toplam K/Z" value={fmtUsd(s.total_pnl, { sign: true })} cls={pnlClass(s.total_pnl)} sub="USDT" />
            <StatTile label="Ort. kazanç / kayıp" value={`${fmtR(s.avg_win_r)} / ${fmtR(s.avg_loss_r)}`} />
            <StatTile label="En iyi işlem" value={fmtR(s.best_r)} cls={pnlClass(s.best_r)} />
            <StatTile label="En kötü işlem" value={fmtR(s.worst_r)} cls={pnlClass(s.worst_r)} />
          </div>
        ) : stats.loading ? (
          <Skeleton className="m-4 h-16" />
        ) : (
          <div className="px-4 py-5 text-[12.5px] text-muted">İstatistikler alınamadı: {stats.error}</div>
        )}
      </Card>

      {s && s.by_strategy.length > 0 && (
        <Card>
          <CardHeader title="Strateji bazında" icon={<BarChart3 className="h-4 w-4" />} />
          <div className="overflow-x-auto pb-1">
            <table className="w-full min-w-[620px] text-[12.5px]">
              <thead className="table-head border-y border-line">
                <tr>
                  <th>Strateji</th>
                  <th className="!text-right">İşlem</th>
                  <th className="!text-right">Kazanma</th>
                  <th className="!text-right">Beklenti</th>
                  <th className="!text-right">Toplam K/Z</th>
                  <th className="w-[30%]">Katkı</th>
                </tr>
              </thead>
              <tbody>
                {s.by_strategy.map((b) => {
                  const maxAbs = Math.max(...s.by_strategy.map((x) => Math.abs(x.total_pnl)), 1e-9);
                  const w = (Math.abs(b.total_pnl) / maxAbs) * 100;
                  return (
                    <tr key={b.strategy} className="table-row border-b border-line/60">
                      <td className="font-medium text-fg">
                        {b.strategy_name} <span className="text-subtle">({b.strategy})</span>
                      </td>
                      <td className="num text-right text-muted">{b.trades}</td>
                      <td className="num text-right text-muted">{b.win_rate != null ? fmtPct(b.win_rate, { digits: 0 }) : '—'}</td>
                      <td className={clsx('num text-right', pnlClass(b.expectancy_r))}>{fmtR(b.expectancy_r)}</td>
                      <td className={clsx('num text-right font-semibold', pnlClass(b.total_pnl))}>{fmtUsd(b.total_pnl, { sign: true })}</td>
                      <td>
                        <div className="h-1.5 w-full rounded-full bg-elevated">
                          <div className={clsx('h-full rounded-full', b.total_pnl >= 0 ? 'bg-up' : 'bg-down')} style={{ width: `${w}%` }} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card>
        <CardHeader title="Kapanan işlemler" icon={<HistoryIcon className="h-4 w-4" />} subtitle={closed.data ? `${closed.data.length} işlem` : undefined} />
        {closed.data && closed.data.length ? (
          <ClosedPositionsTable positions={closed.data} />
        ) : closed.loading ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : (
          <Empty icon={<HistoryIcon className="h-5 w-5" />} title="Henüz kapanan işlem yok">
            İşlemler kapandıkça sonuçları, R değerleri ve kapanış nedenleriyle burada listelenir.
          </Empty>
        )}
      </Card>
    </div>
  );
}
