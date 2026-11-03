import { Layers, Plus } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { Position } from '../api/types';
import { ManualOrderTicket } from '../components/trade/ManualOrderTicket';
import { ClosedPositionsTable, OpenPositionsTable } from '../components/trade/PositionViews';
import { Button, Card, Empty, Skeleton } from '../components/ui/primitives';
import { Pnl, Tabs } from '../components/ui/viz';
import { useAsync } from '../lib/hooks';
import { fmtUsd } from '../lib/format';
import { navigate } from '../lib/router';
import { useApp } from '../store/app';

export function Positions({ tab: routeTab }: { tab?: string }) {
  const open = useApp((s) => s.openPositions);
  const positionsAt = useApp((s) => s.positionsAt);
  const status = useApp((s) => s.status);
  const [tab, setTab] = useState<'open' | 'closed'>(routeTab === 'kapanan' ? 'closed' : 'open');
  const ticketOpen = routeTab === 'yeni';
  const closed = useAsync<Position[]>(() => (tab === 'closed' ? api.positions('closed', 200) : Promise.resolve([])), [tab]);

  useEffect(() => {
    if (routeTab === 'kapanan') setTab('closed');
  }, [routeTab]);

  const totals = useMemo(() => {
    let pnl = 0;
    let margin = 0;
    let notional = 0;
    for (const p of open) {
      pnl += p.pnl_total;
      margin += p.margin;
      notional += (p.mark_price ?? p.entry_price) * p.qty;
    }
    return { pnl, margin, notional };
  }, [open]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">Pozisyonlar</h1>
          <p className="mt-0.5 text-[12.5px] text-muted">Canlı K/Z, stop ve hedef ilerlemesi. Detay ve grafik için bir satıra tıkla.</p>
        </div>
        <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => navigate('/pozisyonlar/yeni')}>
          Yeni işlem
        </Button>
      </div>

      {open.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Card className="p-4">
            <div className="label">Açık pozisyon</div>
            <div className="num mt-1 text-[20px] font-semibold text-fg">
              {open.length}
              {status && <span className="text-[12px] font-medium text-subtle"> / {status.limits.effective_max_positions}</span>}
            </div>
          </Card>
          <Card className="p-4">
            <div className="label">Toplam K/Z</div>
            <div className="mt-1">
              <Pnl value={totals.pnl} size="lg" />
            </div>
          </Card>
          <Card className="p-4">
            <div className="label">Toplam büyüklük</div>
            <div className="num mt-1 text-[20px] font-semibold text-fg">{fmtUsd(totals.notional, { digits: 0 })}</div>
          </Card>
          <Card className="p-4">
            <div className="label">Kullanılan marjin</div>
            <div className="num mt-1 text-[20px] font-semibold text-fg">{fmtUsd(totals.margin)}</div>
          </Card>
        </div>
      )}

      <Card>
        <Tabs
          value={tab}
          onChange={(v) => {
            setTab(v);
            navigate(v === 'closed' ? '/pozisyonlar/kapanan' : '/pozisyonlar');
          }}
          items={[
            { value: 'open', label: 'Açık', count: open.length },
            { value: 'closed', label: 'Kapanan' },
          ]}
          className="px-2"
        />
        {tab === 'open' ? (
          positionsAt === 0 ? (
            <div className="space-y-2 p-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : open.length ? (
            <OpenPositionsTable positions={open} />
          ) : (
            <Empty
              icon={<Layers className="h-5 w-5" />}
              title="Açık pozisyon yok"
              action={
                <Button size="sm" variant="secondary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => navigate('/pozisyonlar/yeni')}>
                  Manuel işlem aç
                </Button>
              }
            >
              Fırsatlar sayfasından bir sinyale girebilir ya da kendi işlemini açabilirsin. Stop-loss her işlemde zorunludur.
            </Empty>
          )
        ) : closed.loading && !closed.data ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : closed.data && closed.data.length ? (
          <ClosedPositionsTable positions={closed.data} />
        ) : (
          <Empty icon={<Layers className="h-5 w-5" />} title="Henüz kapanan işlem yok">
            Kapanan pozisyonlar sonuçlarıyla birlikte burada listelenir.
          </Empty>
        )}
      </Card>

      <ManualOrderTicket open={ticketOpen} onClose={() => navigate(tab === 'closed' ? '/pozisyonlar/kapanan' : '/pozisyonlar')} />
    </div>
  );
}
