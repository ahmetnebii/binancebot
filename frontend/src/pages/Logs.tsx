import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Info, ScrollText, Search, XCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { EventLevel } from '../api/types';
import { Card, Empty, Skeleton } from '../components/ui/primitives';
import { fmtDateTime, fmtTimeSec } from '../lib/format';
import { useApp } from '../store/app';

const LEVELS: { value: EventLevel; label: string; icon: ReactNode; cls: string }[] = [
  { value: 'info', label: 'Bilgi', icon: <Info className="h-3.5 w-3.5" />, cls: 'text-accent' },
  { value: 'success', label: 'Başarılı', icon: <CheckCircle2 className="h-3.5 w-3.5" />, cls: 'text-up' },
  { value: 'warning', label: 'Uyarı', icon: <AlertTriangle className="h-3.5 w-3.5" />, cls: 'text-warn' },
  { value: 'error', label: 'Hata', icon: <XCircle className="h-3.5 w-3.5" />, cls: 'text-down' },
];

export function Logs() {
  const events = useApp((s) => s.events);
  const setEvents = useApp((s) => s.setEvents);
  const [loading, setLoading] = useState(true);
  const [levels, setLevels] = useState<Set<EventLevel>>(new Set(['info', 'success', 'warning', 'error']));
  const [q, setQ] = useState('');

  useEffect(() => {
    api
      .events(500)
      .then((list) => {
        const cur = useApp.getState().events;
        const map = new Map<number, (typeof list)[number]>();
        for (const e of [...list, ...cur]) map.set(e.id, e);
        setEvents([...map.values()].sort((a, b) => b.ts - a.ts));
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [setEvents]);

  const filtered = useMemo(() => {
    const qq = q.trim().toLocaleLowerCase('tr-TR');
    return events.filter((e) => levels.has(e.level) && (!qq || `${e.kind} ${e.message}`.toLocaleLowerCase('tr-TR').includes(qq)));
  }, [events, levels, q]);

  const toggle = (lv: EventLevel) => {
    const next = new Set(levels);
    if (next.has(lv)) next.delete(lv);
    else next.add(lv);
    setLevels(next);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">Sistem günlüğü</h1>
          <p className="mt-0.5 text-[12.5px] text-muted">Sistemin aldığı kararlar, emirler, uyarılar ve hatalar — canlı akış.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {LEVELS.map((l) => (
            <button
              key={l.value}
              type="button"
              onClick={() => toggle(l.value)}
              className={clsx(
                'inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-[12px] font-medium transition-colors',
                levels.has(l.value) ? 'border-line-strong bg-elevated text-fg' : 'border-line text-subtle hover:text-muted',
              )}
            >
              <span className={levels.has(l.value) ? l.cls : ''}>{l.icon}</span>
              {l.label}
            </button>
          ))}
          <div className="flex h-8 items-center gap-2 rounded-lg border border-line bg-bg px-2.5 focus-within:border-accent">
            <Search className="h-3.5 w-3.5 text-subtle" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ara…"
              className="w-40 bg-transparent text-[12.5px] text-fg outline-none placeholder:text-subtle"
            />
          </div>
        </div>
      </div>
      <Card className="overflow-hidden">
        {loading && !events.length ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : filtered.length ? (
          <ul className="divide-y divide-line/70">
            {filtered.map((e) => {
              const meta = LEVELS.find((l) => l.value === e.level) ?? LEVELS[0];
              return (
                <li key={e.id} className="flex items-start gap-3 px-4 py-2.5 hover:bg-hover/40">
                  <span className={clsx('mt-0.5 shrink-0', meta.cls)}>{meta.icon}</span>
                  <span className="num w-[72px] shrink-0 pt-px text-[11.5px] text-subtle" title={fmtDateTime(e.ts)}>
                    {fmtTimeSec(e.ts)}
                  </span>
                  <span lang="en" className="num hidden w-[120px] shrink-0 truncate pt-px text-[11px] uppercase tracking-wide text-subtle md:block">{e.kind}</span>
                  <span className="min-w-0 flex-1 break-words text-[12.5px] text-fg">{e.message}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty icon={<ScrollText className="h-5 w-5" />} title="Kayıt yok">
            Filtrelere uyan olay bulunamadı.
          </Empty>
        )}
      </Card>
    </div>
  );
}
