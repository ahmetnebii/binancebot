import { Info, Radar, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, withToast } from '../api/client';
import type { Signal } from '../api/types';
import { SignalCard } from '../components/trade/SignalCard';
import { SignalDrawer } from '../components/trade/SignalDrawer';
import { Empty, Segmented, Skeleton } from '../components/ui/primitives';
import { Tabs } from '../components/ui/viz';
import { useAsync, useNow } from '../lib/hooks';
import { fmtRelative } from '../lib/format';
import { navigate } from '../lib/router';
import { useApp } from '../store/app';

type SideFilter = 'all' | 'LONG' | 'SHORT';
type Sort = 'score' | 'ev' | 'new';

export function Opportunities({ openId }: { openId?: string }) {
  const status = useApp((s) => s.status);
  const signalsMap = useApp((s) => s.signals);
  const loaded = useApp((s) => s.signalsLoaded);
  const [tab, setTab] = useState<'active' | 'recent'>('active');
  const [side, setSide] = useState<SideFilter>('all');
  const [strategy, setStrategy] = useState<string>('all');
  const [sort, setSort] = useState<Sort>('score');
  const [confirmOnOpen, setConfirmOnOpen] = useState(false);
  const now = useNow(5000);

  const recent = useAsync<Signal[]>(() => (tab === 'recent' ? api.signals('recent') : Promise.resolve([])), [tab]);

  const active = useMemo(
    () => Object.values(signalsMap).filter((s) => s.status === 'ACTIVE' && s.expires_at > now),
    [signalsMap, now],
  );
  const source = tab === 'active' ? active : (recent.data ?? []).map((s) => signalsMap[s.id] ?? s);
  const strategies = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of [...active, ...(recent.data ?? [])]) m.set(s.strategy, s.strategy_name);
    return Array.from(m.entries());
  }, [active, recent.data]);

  const list = useMemo(() => {
    let l = source.filter((s) => (side === 'all' || s.side === side) && (strategy === 'all' || s.strategy === strategy));
    l = [...l].sort((a, b) => (sort === 'score' ? b.score - a.score : sort === 'ev' ? b.ev_r - a.ev_r : b.created_at - a.created_at));
    return l;
  }, [source, side, strategy, sort]);

  const selected = openId ? signalsMap[openId] ?? (recent.data ?? []).find((s) => s.id === openId) ?? null : null;

  const open = (id: string, confirm = false) => {
    setConfirmOnOpen(confirm);
    navigate(`/firsatlar/${id}`);
  };

  const dismiss = async (s: Signal) => {
    try {
      const upd = await withToast(api.dismissSignal(s.id), 'Fırsat yoksayılamadı');
      useApp.getState().upsertSignal(upd);
    } catch {
      /* toast */
    }
  };

  const lastScan = status?.scanner.last_scan_at;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">Fırsatlar</h1>
          <p className="mt-0.5 text-[12.5px] text-muted">
            Tüm piyasa taranır; her fırsat puanlanır, beklenen değeri ve risk planı hesaplanır.
            {lastScan ? ` Son tarama ${fmtRelative(lastScan, now)}.` : ''}
          </p>
        </div>
      </div>

      {status && (
        <div
          className={
            status.auto_mode
              ? 'flex items-start gap-3 rounded-xl border border-up/25 bg-up/[0.06] px-4 py-3 text-[12.5px] text-up'
              : 'flex items-start gap-3 rounded-xl border border-accent/25 bg-accent/[0.06] px-4 py-3 text-[12.5px] text-accent'
          }
        >
          {status.auto_mode ? <Sparkles className="mt-px h-4 w-4 shrink-0" /> : <Info className="mt-px h-4 w-4 shrink-0" />}
          <div>
            <div className="font-semibold">{status.auto_mode ? 'Otomatik mod açık' : 'Otomatik mod kapalı — kontrol sende'}</div>
            <div className="mt-0.5 text-[12px] opacity-90">
              {status.auto_mode
                ? 'Risk kurallarını geçen fırsatlar sistem tarafından otomatik işlenir. İstersen herhangi bir fırsata elle de girebilirsin.'
                : 'Yeni fırsatlar burada görünmeye devam eder. İncele, planı kontrol et ve tek tıkla long/short aç. Otomatik modu üst çubuktan açabilirsin.'}
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { value: 'active', label: 'Aktif', count: active.length },
            { value: 'recent', label: 'Son fırsatlar' },
          ]}
          className="border-b-0"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            size="xs"
            value={side}
            onChange={setSide}
            options={[
              { value: 'all', label: 'Tümü' },
              { value: 'LONG', label: 'Long' },
              { value: 'SHORT', label: 'Short' },
            ]}
          />
          <Segmented
            size="xs"
            value={strategy}
            onChange={setStrategy}
            options={[{ value: 'all', label: 'Tüm stratejiler' }, ...strategies.map(([code, name]) => ({ value: code, label: name }))]}
          />
          <Segmented
            size="xs"
            value={sort}
            onChange={setSort}
            options={[
              { value: 'score', label: 'Puan' },
              { value: 'ev', label: 'Beklenti' },
              { value: 'new', label: 'En yeni' },
            ]}
          />
        </div>
      </div>

      {(tab === 'active' && !loaded) || (tab === 'recent' && recent.loading && !recent.data) ? (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[320px] rounded-xl" />
          ))}
        </div>
      ) : list.length ? (
        <div className="grid items-start gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {list.map((s) => (
            <SignalCard
              key={s.id}
              signal={s}
              autoMode={!!status?.auto_mode}
              onOpen={() => open(s.id)}
              onExecute={() => open(s.id, true)}
              onDismiss={() => void dismiss(s)}
            />
          ))}
        </div>
      ) : (
        <div className="card">
          <Empty icon={<Radar className="h-5 w-5" />} title={tab === 'active' ? 'Şu an aktif fırsat yok' : 'Kayıtlı fırsat yok'}>
            {tab === 'active'
              ? 'Tarayıcı her mum kapanışında tüm piyasayı yeniden değerlendirir. Yeni bir fırsat geldiğinde burada ve bildirimlerde görünür.'
              : 'Geçmiş fırsatlar burada listelenir.'}
          </Empty>
        </div>
      )}

      <SignalDrawer
        signal={selected}
        confirmOnOpen={confirmOnOpen}
        onClose={() => {
          setConfirmOnOpen(false);
          navigate('/firsatlar');
        }}
      />
    </div>
  );
}
