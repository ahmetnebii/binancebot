import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, FlaskConical, Loader2, Rocket, ShieldCheck, Square, Trophy, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { LabJob, LabResult, LabRow, LabStats } from '../../api/types';
import { useAsync, useNow } from '../../lib/hooks';
import { fmtDate, fmtDateTime, fmtDuration, fmtNum, fmtPct, fmtR, pnlClass } from '../../lib/format';
import { toast, useApp } from '../../store/app';
import { EquityDrawdownChart } from '../analytics/EquityDrawdownChart';
import { InfoTip, useConfirm } from '../ui/overlay';
import { Badge, Button, Card, CardHeader, Segmented } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const pctU = (v: number | null | undefined) => (isNum(v) ? fmtPct(v / 100, { sign: true, digits: 1 }) : '—');
const nf = (n: number) => n.toLocaleString('tr-TR');

const BUDGETS = [
  { value: 'hizli', label: 'Hızlı', n: 2000, hint: '~3-10 dk' },
  { value: 'normal', label: 'Normal', n: 10000, hint: '~10-30 dk' },
  { value: 'derin', label: 'Derin', n: 30000, hint: '~30-90 dk' },
  { value: 'maksimum', label: 'Maksimum', n: 100000, hint: '~1,5-5 saat' },
] as const;

const VERDICT: Record<LabResult['verdict'], { title: string; icon: JSX.Element; cls: string }> = {
  recommended: { title: 'Avantajlı kural bulundu', icon: <CheckCircle2 className="h-5 w-5 text-up" />, cls: 'border-up/30 bg-up/[0.06]' },
  weak: { title: 'Zayıf avantajlı kural bulundu', icon: <AlertTriangle className="h-5 w-5 text-warn" />, cls: 'border-warn/30 bg-warn/[0.06]' },
  none: { title: 'Güvenilir avantaj bulunamadı', icon: <XCircle className="h-5 w-5 text-down" />, cls: 'border-down/30 bg-down/[0.06]' },
};

const active = (j: { status: string }) => j.status === 'queued' || j.status === 'running';

function StageRow({ name, sub, s, strong }: { name: string; sub: string; s: LabStats | null | undefined; strong?: boolean }) {
  return (
    <tr className={clsx('border-t border-line', strong && 'bg-accent/[0.05] font-medium')}>
      <td className="px-2 py-1.5">
        <div className="text-fg">{name}</div>
        <div className="text-[11px] text-subtle">{sub}</div>
      </td>
      <td className="num px-2 py-1.5 text-right">{s ? s.trades : '—'}</td>
      <td className={clsx('num px-2 py-1.5 text-right', pnlClass(s?.expectancy_r ?? 0))}>{fmtR(s?.expectancy_r)}</td>
      <td className="num px-2 py-1.5 text-right">{isNum(s?.profit_factor) ? fmtNum(s!.profit_factor, 2) : '—'}</td>
      <td className="num px-2 py-1.5 text-right">{isNum(s?.win_rate) ? fmtPct(s!.win_rate, { digits: 0 }) : '—'}</td>
      <td className="num px-2 py-1.5 text-right">{s ? fmtNum(s.t_stat, 2) : '—'}</td>
      <td className={clsx('num px-2 py-1.5 text-right', pnlClass(s?.return_pct ?? 0))}>{isNum(s?.return_pct) ? pctU(s!.return_pct) : '—'}</td>
    </tr>
  );
}

function Stages({ r }: { r: LabResult }) {
  const b = r.best;
  const p = r.period;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[12px]">
        <thead>
          <tr className="text-[11px] text-subtle">
            <th className="px-2 py-1 text-left font-medium">Aşama</th>
            <th className="px-2 py-1 text-right font-medium">İşlem</th>
            <th className="px-2 py-1 text-right font-medium">Beklenti</th>
            <th className="px-2 py-1 text-right font-medium">PF</th>
            <th className="px-2 py-1 text-right font-medium">Kazanma</th>
            <th className="px-2 py-1 text-right font-medium">t-değeri</th>
            <th className="px-2 py-1 text-right font-medium">Getiri</th>
          </tr>
        </thead>
        <tbody>
          <StageRow name="1 · Eğitim (hızlı tarama)" sub={`${fmtDate(p.start)} – ${fmtDate(p.split1)} · seçim burada`} s={b?.is} />
          <StageRow name="2 · Doğrulama (hızlı tarama)" sub={`${fmtDate(p.split1)} – ${fmtDate(p.split2)} · eleme`} s={b?.oos} />
          <StageRow name="3 · Gerçekçi portföy simülasyonu" sub="eğitim + doğrulama · limitler, komisyon, funding" s={b?.dev} />
          <StageRow name="4 · Final sınavı" sub={`${fmtDate(p.split2)} – ${fmtDate(p.end)} · aramada hiç görülmedi`} s={b?.final} strong />
        </tbody>
      </table>
    </div>
  );
}

function Leaderboard({ rows }: { rows: LabRow[] }) {
  if (!rows.length) return null;
  return (
    <details className="rounded-xl border border-line">
      <summary className="cursor-pointer select-none px-3 py-2 text-[12.5px] font-medium text-muted hover:text-fg">
        Eğitimde en iyi {rows.length} kural (doğrulama sonuçlarıyla)
      </summary>
      <div className="overflow-x-auto px-1 pb-2">
        <table className="w-full min-w-[620px] text-[12px]">
          <thead>
            <tr className="text-[11px] text-subtle">
              <th className="px-2 py-1 text-left font-medium">Kural</th>
              <th className="px-2 py-1 text-right font-medium">Eğitim t</th>
              <th className="px-2 py-1 text-right font-medium">Eğitim işlem</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. işlem</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. beklenti</th>
              <th className="px-2 py-1 text-right font-medium">Doğr. PF</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                <td className="px-2 py-1.5 text-fg" title={r.details.join('\n')}>{r.label}</td>
                <td className="num px-2 py-1.5 text-right">{fmtNum(r.is.t_stat, 2)}</td>
                <td className="num px-2 py-1.5 text-right">{r.is.trades}</td>
                <td className="num px-2 py-1.5 text-right">{r.oos?.trades ?? '—'}</td>
                <td className={clsx('num px-2 py-1.5 text-right', pnlClass(r.oos?.expectancy_r ?? 0))}>{fmtR(r.oos?.expectancy_r)}</td>
                <td className="num px-2 py-1.5 text-right">{isNum(r.oos?.profit_factor) ? fmtNum(r.oos!.profit_factor, 2) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function Running({ job, onCancel, cancelling }: { job: LabJob; onCancel: () => void; cancelling: boolean }) {
  const now = useNow(1000);
  const pct = Math.max(0, Math.min(100, job.progress.pct));
  const elapsed = now - job.created_at;
  const eta = pct > 12 ? ((elapsed / pct) * (100 - pct)) : null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-fg">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          {job.status === 'queued' ? 'Sırada…' : 'Laboratuvar çalışıyor'}
          <span className="text-[11.5px] text-subtle">
            {job.config.days} gün · ilk {job.config.top_n} coin · {nf(job.config.candidates)} kural · {fmtDuration(elapsed)} geçti
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
      <p className="text-[11.5px] text-subtle">Sayfayı kapatabilirsin; arama sunucuda sürer. Bilgisayarı uyku moduna alma ve bot penceresini kapatma.</p>
    </div>
  );
}

function ResultPanel({ job, onApply, applying }: { job: LabJob; onApply: () => void; applying: boolean }) {
  const r = job.result!;
  const v = VERDICT[r.verdict];
  const b = r.best;
  return (
    <div className="space-y-4">
      <div className={clsx('flex flex-wrap items-start gap-3 rounded-xl border px-4 py-3', v.cls)}>
        {v.icon}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-fg">{v.title}</span>
            {b && <Badge tone="accent">{b.label}</Badge>}
            {job.applied_at && (
              <Badge tone="up">
                <ShieldCheck className="mr-1 inline h-3 w-3" />
                Uygulandı
              </Badge>
            )}
          </div>
          <p className="mt-1 text-[12.5px] text-muted">{r.verdict_text}</p>
          <p className="mt-1 text-[11.5px] text-subtle">
            {nf(r.tested)} kural denendi → {r.shortlist} güçlü aday → {r.passed_oos} tanesi doğrulamada da kârlı → {r.finalists.length} finalist
            gerçekçi simülasyonda · şans çıtası t ≈ {fmtNum(r.luck_bar, 2)} · {fmtDateTime(job.finished_at)}
          </p>
        </div>
        {r.verdict !== 'none' && b && (
          <Button variant="success" icon={<Rocket className="h-4 w-4" />} onClick={onApply} loading={applying}>
            Bu kuralı uygula
          </Button>
        )}
      </div>

      {b && (
        <div className="rounded-xl border border-line px-4 py-3">
          <div className="label mb-1.5 flex items-center gap-1">
            <Trophy className="h-3.5 w-3.5" /> Bulunan kural
            {isNum(b.is.t_stat) && (
              <span className={clsx('ml-2 normal-case', b.is.t_stat >= r.luck_bar ? 'text-up' : 'text-warn')}>
                eğitim t = {fmtNum(b.is.t_stat, 2)} {b.is.t_stat >= r.luck_bar ? '≥' : '<'} şans çıtası {fmtNum(r.luck_bar, 2)}
              </span>
            )}
          </div>
          <ul className="space-y-1 text-[12.5px] text-fg">
            {b.details.map((d) => (
              <li key={d}>• {d}</li>
            ))}
          </ul>
        </div>
      )}

      {b && <Stages r={r} />}

      {r.full && r.full.equity?.length > 1 && (
        <div>
          <div className="label mb-1.5 flex items-center gap-1">
            Bu kuralla tüm dönem özsermaye (gerçekçi simülasyon)
            <InfoTip>Eğitim + doğrulama + final sınavı birlikte. Karar yalnızca final sınavına göre verilir.</InfoTip>
          </div>
          <EquityDrawdownChart data={r.full.equity} initial={r.full.summary.initial_equity} height={260} />
        </div>
      )}

      <Leaderboard rows={r.leaderboard} />

      <ul className="space-y-1 text-[11.5px] text-subtle">
        {r.warnings.map((w) => (
          <li key={w} className="flex gap-1.5">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            {w}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function StrategyLab({ blocked, onApplied }: { blocked: boolean; onApplied: () => void }) {
  const jobsMap = useApp((s) => s.backtestJobs);
  const [days, setDays] = useState('365');
  const [budget, setBudget] = useState<string>('derin');
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [applying, setApplying] = useState(false);
  const { confirm, node } = useConfirm();

  const list = useAsync<LabJob[]>(
    () =>
      api.labJobs().then((l) => {
        const st = useApp.getState();
        l.forEach((j) => st.upsertBacktestJob(j as never));
        return l;
      }),
    [],
  );
  const latest = useMemo(
    () => (Object.values(jobsMap) as unknown as LabJob[]).filter((j) => j.kind === 'lab').sort((a, b) => b.created_at - a.created_at)[0],
    [jobsMap],
  );
  const full = useAsync<LabJob | undefined>(
    () => (latest && latest.status === 'done' ? api.labJob(latest.id) : Promise.resolve(undefined)),
    [latest?.id, latest?.status, latest?.applied_at],
  );
  const shown = full.data && full.data.id === latest?.id ? full.data : latest;

  const start = async () => {
    setBusy(true);
    try {
      const j = await withToast(api.runLab({ days: Number(days), top_n: 30, budget }), 'Laboratuvar başlatılamadı');
      useApp.getState().upsertBacktestJob(j as never);
      toast.success('Laboratuvar başladı — bitince sonuç burada görünecek');
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
      title: 'Bulunan kural uygulansın mı?',
      message:
        'Doğrulanan, bu kuralın TEK BAŞINA çalışmasıdır: S1–S5 stratejileri kapatılır, canlı tarama yalnızca bu kuralla fırsat arar. İstediğin zaman Ayarlar’dan geri açabilirsin.',
      details: (
        <ul className="space-y-0.5 text-[12px]">
          {r.best.details.map((d) => (
            <li key={d}>• {d}</li>
          ))}
        </ul>
      ),
      confirmText: 'Uygula',
      tone: 'success',
    });
    if (!ok) return;
    setApplying(true);
    try {
      await withToast(api.applyLab(shown.id), 'Kural uygulanamadı');
      toast.success('Kural uygulandı — tarama artık bu kuralla çalışıyor');
      full.reload();
      onApplied();
    } catch {
      /* toast */
    } finally {
      setApplying(false);
    }
  };

  const running = latest && active(latest);
  const sel = BUDGETS.find((b) => b.value === budget);

  return (
    <Card className="overflow-hidden border-accent/30">
      {node}
      <CardHeader
        title="Strateji Laboratuvarı"
        subtitle="Binlerce giriş / filtre / çıkış kombinasyonu (11 giriş tipi × 7 filtre × stop × hedef × iz süren stop × 3 zaman dilimi) taranır; kazanan, aramada hiç görülmemiş dönemde final sınavına girer."
        icon={<FlaskConical className="h-4 w-4 text-accent" />}
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
                Kural sayısı{' '}
                <span className="normal-case text-subtle">
                  ({sel ? `${nf(sel.n)} kural · ${sel.hint}` : ''})
                </span>
              </div>
              <Segmented value={budget} onChange={setBudget} options={BUDGETS.map((b) => ({ value: b.value, label: b.label }))} />
            </div>
            <Button variant="primary" size="lg" icon={<FlaskConical className="h-4 w-4" />} onClick={start} loading={busy} disabled={blocked} className="ml-auto">
              En iyi stratejiyi bul
            </Button>
            {blocked && <p className="w-full text-right text-[11.5px] text-subtle">Çalışan başka bir iş bitince başlatabilirsin.</p>}
          </div>
        )}

        {!running && shown?.status === 'done' && shown.result && <ResultPanel job={shown} onApply={apply} applying={applying} />}
        {!running && shown?.status === 'done' && !shown.result && full.loading && (
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Sonuç yükleniyor…
          </div>
        )}
        {!running && shown?.status === 'failed' && <p className="text-[12px] text-down">Son arama başarısız: {shown.error || 'bilinmeyen hata'}</p>}
        {!running && shown?.status === 'cancelled' && <p className="text-[12px] text-subtle">Son arama durduruldu.</p>}
        {!shown && !list.loading && (
          <p className="text-[12px] leading-relaxed text-muted">
            Nasıl çalışır: geçmiş üçe bölünür — <b>eğitim</b> (%60) içinde binlerce kural denenir ve en iyileri evrimsel olarak iyileştirilir;
            <b> doğrulama</b> (%20) şans eseri iyi görünenleri eler; finalistler canlıyla aynı portföy simülatöründe test edilir; kazanan
            aramada <b>hiç görülmemiş final sınavında</b> (%20) son kez sınanır. Kâr tek bir dönemden veya tek bir coinden geliyorsa kural elenir.
          </p>
        )}
      </div>
    </Card>
  );
}
