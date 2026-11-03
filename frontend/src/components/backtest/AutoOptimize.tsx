import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Loader2, Rocket, ShieldCheck, Square, Trophy, Wand2, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { OptimizeEval, OptimizeJob, OptimizeResult, OptimizeRow } from '../../api/types';
import { useAsync, useNow } from '../../lib/hooks';
import { fmtDate, fmtDateTime, fmtDuration, fmtNum, fmtPct, fmtR, pnlClass } from '../../lib/format';
import { toast, useApp } from '../../store/app';
import { EquityDrawdownChart } from '../analytics/EquityDrawdownChart';
import { InfoTip, useConfirm } from '../ui/overlay';
import { Badge, Button, Card, CardHeader, Segmented } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const pctU = (v: number | null | undefined) => (isNum(v) ? fmtPct(v / 100, { sign: true, digits: 1 }) : '—');

const BUDGETS = [
  { value: 'hizli', label: 'Hızlı', hint: '16 aday · ~3-8 dk' },
  { value: 'normal', label: 'Normal', hint: '32 aday · ~8-20 dk' },
  { value: 'derin', label: 'Derin', hint: '64 aday · ~20-45 dk' },
] as const;

const VERDICT: Record<OptimizeResult['verdict'], { title: string; tone: 'up' | 'warn' | 'down'; icon: JSX.Element; cls: string }> = {
  recommended: { title: 'Önerilen ayar bulundu', tone: 'up', icon: <CheckCircle2 className="h-5 w-5 text-up" />, cls: 'border-up/30 bg-up/[0.06]' },
  weak: { title: 'Zayıf avantaj bulundu', tone: 'warn', icon: <AlertTriangle className="h-5 w-5 text-warn" />, cls: 'border-warn/30 bg-warn/[0.06]' },
  none: { title: 'Güvenilir avantaj bulunamadı', tone: 'down', icon: <XCircle className="h-5 w-5 text-down" />, cls: 'border-down/30 bg-down/[0.06]' },
};

const active = (j: { status: string }) => j.status === 'queued' || j.status === 'running';

/* ------------------------------------------------------------------ */

function EvalCells({ e }: { e: OptimizeEval | undefined }) {
  if (!e) return <td colSpan={4} className="px-2 py-1.5 text-subtle">—</td>;
  return (
    <>
      <td className="num px-2 py-1.5 text-right">{e.trades}</td>
      <td className={clsx('num px-2 py-1.5 text-right', pnlClass(e.expectancy_r ?? 0))}>{fmtR(e.expectancy_r)}</td>
      <td className="num px-2 py-1.5 text-right">{isNum(e.profit_factor) ? fmtNum(e.profit_factor, 2) : '—'}</td>
      <td className={clsx('num px-2 py-1.5 text-right', pnlClass(e.return_pct))}>{pctU(e.return_pct)}</td>
    </>
  );
}

function CompareTable({ best, baseline }: { best: OptimizeRow | null; baseline: OptimizeRow | null }) {
  const rows: { name: string; row: OptimizeRow | null; strong?: boolean }[] = [
    { name: 'Şu anki ayarların', row: baseline },
    { name: 'Bulunan en iyi', row: best, strong: true },
  ];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] text-[12px]">
        <thead>
          <tr className="text-[11px] text-subtle">
            <th className="px-2 py-1 text-left font-medium" rowSpan={2}>Ayar</th>
            <th className="border-l border-line px-2 py-1 text-center font-medium" colSpan={4}>
              Eğitim dönemi <span className="text-subtle">(seçim burada)</span>
            </th>
            <th className="border-l border-line px-2 py-1 text-center font-medium" colSpan={4}>
              Doğrulama dönemi <span className="text-fg">(karar burada)</span>
            </th>
          </tr>
          <tr className="text-[11px] text-subtle">
            {[0, 1].map((k) => (
              <FragmentHeads key={k} />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, row, strong }) => (
            <tr key={name} className={clsx('border-t border-line', strong && 'bg-accent/[0.05] font-medium')}>
              <td className="px-2 py-1.5">
                <div className="text-fg">{name}</div>
                <div className="text-[11px] text-subtle">{row?.label || '—'}</div>
              </td>
              <EvalCells e={row?.is} />
              <EvalCells e={row?.oos} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FragmentHeads() {
  return (
    <>
      <th className="border-l border-line px-2 py-1 text-right font-medium">İşlem</th>
      <th className="px-2 py-1 text-right font-medium">Beklenti</th>
      <th className="px-2 py-1 text-right font-medium">PF</th>
      <th className="px-2 py-1 text-right font-medium">Getiri</th>
    </>
  );
}

function Leaderboard({ rows }: { rows: OptimizeRow[] }) {
  if (!rows.length) return null;
  return (
    <details className="group rounded-xl border border-line">
      <summary className="cursor-pointer select-none px-3 py-2 text-[12.5px] font-medium text-muted hover:text-fg">
        Eğitimde en iyi {rows.length} aday (doğrulama sonuçlarıyla)
      </summary>
      <div className="overflow-x-auto px-1 pb-2">
        <table className="w-full min-w-[640px] text-[12px]">
          <thead>
            <tr className="text-[11px] text-subtle">
              <th className="px-2 py-1 text-left font-medium">#</th>
              <th className="px-2 py-1 text-left font-medium">Ayar</th>
              <th className="px-2 py-1 text-right font-medium">Eğitim t</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. işlem</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. beklenti</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. PF</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. getiri</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.rank} className="border-t border-line align-top">
                <td className="num px-2 py-1.5 text-subtle">{r.rank}</td>
                <td className="px-2 py-1.5">
                  <div className="text-fg">{r.label}</div>
                  <div className="text-[11px] text-subtle">{r.details.join(' · ')} · min EV {fmtR(r.min_ev_r)}</div>
                </td>
                <td className="num px-2 py-1.5 text-right">{fmtNum(r.is.t_stat, 2)}</td>
                <td className="num px-2 py-1.5 text-right">{r.oos.trades}</td>
                <td className={clsx('num px-2 py-1.5 text-right', pnlClass(r.oos.expectancy_r ?? 0))}>{fmtR(r.oos.expectancy_r)}</td>
                <td className="num px-2 py-1.5 text-right">{isNum(r.oos.profit_factor) ? fmtNum(r.oos.profit_factor, 2) : '—'}</td>
                <td className={clsx('num px-2 py-1.5 text-right', pnlClass(r.oos.return_pct))}>{pctU(r.oos.return_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/* ------------------------------------------------------------------ */

function Running({ job, onCancel, cancelling }: { job: OptimizeJob; onCancel: () => void; cancelling: boolean }) {
  const now = useNow(1000);
  const pct = Math.max(0, Math.min(100, job.progress.pct));
  const elapsed = now - job.created_at;
  const eta = pct > 3 ? (elapsed / pct) * (100 - pct) : null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[13px] text-fg">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          {job.status === 'queued' ? 'Sırada…' : 'En iyi ayarlar aranıyor'}
          <span className="text-[11.5px] text-subtle">
            {job.config.days} gün · ilk {job.config.top_n} coin · {job.config.candidates} aday · {fmtDuration(elapsed)} geçti
            {eta !== null && ` · ~${fmtDuration(eta)} kaldı`}
          </span>
        </div>
        <Button size="sm" variant="outline-danger" icon={<Square className="h-3.5 w-3.5" />} onClick={onCancel} loading={cancelling}>
          Durdur
        </Button>
      </div>
      <div>
        <div className="mb-1.5 flex items-center justify-between text-[12px]">
          <span className="text-muted">{job.progress.message || 'Hazırlanıyor…'}</span>
          <span className="num font-semibold text-fg">%{Math.round(pct)}</span>
        </div>
        <div className="relative h-2 w-full overflow-hidden rounded-full bg-elevated">
          <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <p className="text-[11.5px] text-subtle">Sayfayı kapatabilirsin; arama sunucuda sürer ve sonuç burada görünür.</p>
    </div>
  );
}

function ResultPanel({ job, onApply, applying }: { job: OptimizeJob; onApply: () => void; applying: boolean }) {
  const r = job.result!;
  const v = VERDICT[r.verdict];
  const canApply = r.verdict !== 'none' && !!r.best;
  return (
    <div className="space-y-4">
      <div className={clsx('flex items-start gap-3 rounded-xl border px-4 py-3', v.cls)}>
        {v.icon}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-fg">{v.title}</span>
            {r.best && <Badge tone="accent">{r.best.label}</Badge>}
            {job.applied_at && (
              <Badge tone="up">
                <ShieldCheck className="mr-1 inline h-3 w-3" />
                Uygulandı
              </Badge>
            )}
          </div>
          <p className="mt-1 text-[12.5px] text-muted">{r.verdict_text}</p>
          <p className="mt-1 text-[11.5px] text-subtle">
            {r.candidates_tested} aday denendi · eğitim {fmtDate(r.period.start)} – {fmtDate(r.period.split)} · doğrulama {fmtDate(r.period.split)} –{' '}
            {fmtDate(r.period.end)} · {fmtDateTime(job.finished_at)}
          </p>
        </div>
        {r.verdict !== 'none' && r.best && (
          <Button variant="success" icon={<Rocket className="h-4 w-4" />} onClick={onApply} loading={applying} disabled={!canApply}>
            Bu ayarları uygula
          </Button>
        )}
      </div>

      {r.best && (
        <div className="rounded-xl border border-line px-4 py-3">
          <div className="label mb-1.5 flex items-center gap-1">
            <Trophy className="h-3.5 w-3.5" /> Bulunan ayarlar
          </div>
          <ul className="space-y-1 text-[12.5px] text-fg">
            {r.best.details.map((d) => (
              <li key={d}>• {d}</li>
            ))}
            <li>• Minimum beklenen değer: {fmtR(r.best.min_ev_r)}</li>
          </ul>
        </div>
      )}

      <CompareTable best={r.best} baseline={r.baseline} />

      {r.full && r.full.equity?.length > 1 && (
        <div>
          <div className="label mb-1.5 flex items-center gap-1">
            Bulunan ayarlarla tüm dönem özsermaye
            <InfoTip>Eğitim + doğrulama dönemi birlikte. Kararı yalnızca doğrulama dönemi belirler.</InfoTip>
          </div>
          <EquityDrawdownChart data={r.full.equity} initial={r.full.summary.initial_equity} height={260} />
        </div>
      )}

      <Leaderboard rows={r.leaderboard} />

      {r.warnings.length > 0 && (
        <ul className="space-y-1 text-[11.5px] text-subtle">
          {r.warnings.map((w) => (
            <li key={w} className="flex gap-1.5">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

export function AutoOptimize({ blocked, onApplied }: { blocked: boolean; onApplied: () => void }) {
  const jobsMap = useApp((s) => s.backtestJobs);
  const [days, setDays] = useState('180');
  const [budget, setBudget] = useState<string>('normal');
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [applying, setApplying] = useState(false);
  const { confirm, node } = useConfirm();

  const list = useAsync<OptimizeJob[]>(
    () =>
      api.optimizeJobs().then((l) => {
        const st = useApp.getState();
        l.forEach((j) => st.upsertBacktestJob(j as never));
        return l;
      }),
    [],
  );
  const latest = useMemo(
    () =>
      (Object.values(jobsMap) as unknown as OptimizeJob[])
        .filter((j) => j.kind === 'optimize')
        .sort((a, b) => b.created_at - a.created_at)[0],
    [jobsMap],
  );
  // Sonuç WS ile gelmez; bittiğinde tam işi çek.
  const full = useAsync<OptimizeJob | undefined>(
    () => (latest && latest.status === 'done' ? api.optimizeJob(latest.id) : Promise.resolve(undefined)),
    [latest?.id, latest?.status, latest?.applied_at],
  );
  const shown = full.data && full.data.id === latest?.id ? full.data : latest;

  const start = async () => {
    setBusy(true);
    try {
      const j = await withToast(api.runOptimize({ days: Number(days), top_n: 30, budget }), 'Optimizasyon başlatılamadı');
      useApp.getState().upsertBacktestJob(j as never);
      toast.success('Arama başladı — bitince sonuç burada görünecek');
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!latest) return;
    setCancelling(true);
    try {
      await withToast(api.cancelBacktest(latest.id), 'Durdurulamadı');
    } catch {
      /* toast */
    } finally {
      setCancelling(false);
    }
  };

  const apply = async () => {
    const r = shown?.result;
    if (!shown || !r?.best) return;
    const ok = await confirm({
      title: 'Bulunan ayarlar uygulansın mı?',
      message: 'Strateji ayarları, yön izinleri ve minimum EV güncellenir; kalibrasyon bu sonuçlarla yenilenir. Canlı tarama bundan sonra bu ayarlarla fırsat arar.',
      details: (
        <ul className="space-y-0.5 text-[12px]">
          {r.best.details.map((d) => (
            <li key={d}>• {d}</li>
          ))}
          <li>• Minimum EV: {fmtR(r.best.min_ev_r)}</li>
        </ul>
      ),
      confirmText: 'Uygula',
      tone: 'success',
    });
    if (!ok) return;
    setApplying(true);
    try {
      await withToast(api.applyOptimize(shown.id), 'Ayarlar uygulanamadı');
      toast.success('Ayarlar uygulandı — tarama artık bu ayarlarla çalışıyor');
      full.reload();
      onApplied();
    } catch {
      /* toast */
    } finally {
      setApplying(false);
    }
  };

  const running = latest && active(latest);
  const budgetHint = BUDGETS.find((b) => b.value === budget)?.hint;

  return (
    <Card className="overflow-hidden">
      {node}
      <CardHeader
        title="Otomatik ayar bulucu"
        subtitle="Tek tuş: yüzlerce strateji/yön/parametre kombinasyonu geçmişte denenir, en iyisi görülmemiş veride doğrulanır."
        icon={<Wand2 className="h-4 w-4 text-accent" />}
      />
      <div className="space-y-4 px-4 pb-5 pt-1">
        {running ? (
          <Running job={latest} onCancel={cancel} cancelling={cancelling} />
        ) : (
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <div className="label mb-1">Geçmiş</div>
              <Segmented
                value={days}
                onChange={setDays}
                options={[
                  { value: '180', label: '180 gün' },
                  { value: '365', label: '365 gün' },
                ]}
              />
            </div>
            <div>
              <div className="label mb-1 flex items-center gap-1">
                Derinlik <span className="normal-case text-subtle">({budgetHint})</span>
              </div>
              <Segmented value={budget} onChange={setBudget} options={BUDGETS.map((b) => ({ value: b.value, label: b.label }))} />
            </div>
            <Button size="lg" icon={<Wand2 className="h-4 w-4" />} onClick={start} loading={busy} disabled={blocked} className="ml-auto">
              En iyi ayarları bul
            </Button>
            {blocked && <p className="w-full text-right text-[11.5px] text-subtle">Çalışan bir backtest bitince başlatabilirsin.</p>}
          </div>
        )}

        {!running && shown?.status === 'done' && shown.result && <ResultPanel job={shown} onApply={apply} applying={applying} />}
        {!running && shown?.status === 'done' && !shown.result && full.loading && (
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Sonuç yükleniyor…
          </div>
        )}
        {!running && shown?.status === 'failed' && (
          <p className="text-[12px] text-down">Son arama başarısız: {shown.error || 'bilinmeyen hata'}</p>
        )}
        {!running && shown?.status === 'cancelled' && <p className="text-[12px] text-subtle">Son arama durduruldu.</p>}
        {!shown && !list.loading && (
          <p className="text-[12px] text-muted">
            Ne yapar: veriyi ikiye böler — ilk 2/3'te adayları dener, son 1/3'te (hiç görmediği dönem) en iyileri sınar. Doğrulamada da kârlıysa
            "Önerilen" der; değilse dürüstçe "avantaj yok" der. Sonra tek tuşla uygularsın.
          </p>
        )}
      </div>
    </Card>
  );
}
