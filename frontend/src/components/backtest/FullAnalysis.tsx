import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Circle, FlaskRound, Loader2, Rocket, Sparkles, Square, Trophy, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { FullCandidate, FullJob, FullStep } from '../../api/types';
import { useAsync, useNow } from '../../lib/hooks';
import { fmtDateTime, fmtDuration, fmtNum, pnlClass } from '../../lib/format';
import { toast, useApp } from '../../store/app';
import { useConfirm } from '../ui/overlay';
import { Badge, Button, Card, CardHeader } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const pct = (v: number | null | undefined, d = 1) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '-' : ''}%${Math.abs(v).toFixed(d).replace('.', ',')}` : '—');
const active = (j: { status: string }) => j.status === 'queued' || j.status === 'running';

const VERDICT_LABEL = (c: FullCandidate) =>
  c.verdict === 'recommended' ? 'Önerilen' : c.verdict === 'weak' ? 'Zayıf avantaj' : c.forward_ok ? 'Denemeye değer' : 'Elendi';
const VERDICT_TONE = (c: FullCandidate): 'up' | 'warn' | 'proj' | 'down' =>
  c.verdict === 'recommended' ? 'up' : c.verdict === 'weak' ? 'warn' : c.forward_ok ? 'proj' : 'down';

function StepIcon({ s }: { s: FullStep['status'] }) {
  if (s === 'done') return <CheckCircle2 className="h-4 w-4 text-up" />;
  if (s === 'running') return <Loader2 className="h-4 w-4 animate-spin text-accent" />;
  if (s === 'failed') return <XCircle className="h-4 w-4 text-down" />;
  if (s === 'cancelled') return <XCircle className="h-4 w-4 text-subtle" />;
  return <Circle className="h-4 w-4 text-subtle" />;
}

function Steps({ steps }: { steps: FullStep[] }) {
  return (
    <ol className="grid gap-1.5 sm:grid-cols-2">
      {steps.map((s, i) => (
        <li
          key={s.key}
          className={clsx(
            'flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px]',
            s.status === 'running' ? 'border-accent/40 bg-accent/[0.05]' : s.status === 'done' ? 'border-up/20' : 'border-line',
          )}
        >
          <span className="mt-0.5">
            <StepIcon s={s.status} />
          </span>
          <span className="min-w-0">
            <span className={clsx('block', s.status === 'pending' ? 'text-muted' : 'text-fg')}>
              {i + 1}. {s.name}
            </span>
            {s.status === 'failed' && s.error && <span className="block text-[11px] text-down">{s.error}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

function CandidateActions({ c, onDone }: { c: FullCandidate; onDone: () => void }) {
  const { confirm, node } = useConfirm();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (mode: 'paper' | 'live' | 'carry') => {
    const ok = await confirm({
      title: mode === 'carry' ? 'Funding kuralı radara uygulansın mı?' : mode === 'paper' ? 'Simülasyonda canlı denensin mi?' : 'Gerçekte uygulansın mı?',
      message:
        mode === 'carry'
          ? 'Kural canlı funding radarına ve sanal funding portföyüne bağlanır. Gerçek para kullanılmaz; gerçekte uygulama adımları Funding sekmesinde.'
          : mode === 'paper'
          ? 'Bu analizdeki kurallar deneme olarak uygulanır: otomatik işlem yalnızca Simülasyon ortamında (sanal para) açılır. S1–S5 kapatılır.'
          : 'Doğrulanan kurallar canlı taramaya uygulanır; S1–S5 kapatılır. Gerçek ortamda otomatik mod açıksa gerçek işlem açılabilir.',
      confirmText: 'Uygula',
      tone: mode === 'live' ? 'danger' : 'success',
    });
    if (!ok) return;
    setBusy(mode);
    try {
      if (mode === 'carry') await withToast(api.applyCarry(c.job_id), 'Uygulanamadı');
      else await withToast(api.applyLab(c.job_id, mode), 'Uygulanamadı');
      toast.success('Uygulandı');
      onDone();
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex flex-wrap gap-2">
      {node}
      {c.kind === 'carry' ? (
        <Button size="sm" variant="success" icon={<Rocket className="h-3.5 w-3.5" />} onClick={() => run('carry')} loading={busy === 'carry'}>
          Radara uygula
        </Button>
      ) : (
        <>
          <Button size="sm" variant="secondary" icon={<FlaskRound className="h-3.5 w-3.5" />} onClick={() => run('paper')} loading={busy === 'paper'}>
            Simülasyonda dene
          </Button>
          {c.verdict !== 'none' && (
            <Button size="sm" variant="success" icon={<Rocket className="h-3.5 w-3.5" />} onClick={() => run('live')} loading={busy === 'live'}>
              Gerçekte uygula
            </Button>
          )}
        </>
      )}
    </div>
  );
}

function HeroCard({ c, title, tone, onDone }: { c: FullCandidate; title: string; tone: 'up' | 'proj'; onDone: () => void }) {
  return (
    <div className={clsx('rounded-xl border p-4', tone === 'up' ? 'border-up/40 bg-up/[0.06]' : 'border-proj/40 bg-proj/[0.06]')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-muted">
            <Trophy className="h-4 w-4" /> {title}
          </div>
          <div className="mt-1 text-[16px] font-semibold text-fg">{c.title}</div>
          <div className="text-[12.5px] text-muted">{c.label}</div>
        </div>
        <div className="text-right">
          <div className={clsx('num text-[26px] font-bold', pnlClass(c.oos_apr_pct))}>{pct(c.oos_apr_pct)}</div>
          <div className="text-[11px] text-subtle">yıllık · hiç görülmemiş {Math.round(c.oos_days)} günde</div>
        </div>
      </div>
      <ul className="mt-3 space-y-0.5 text-[12px] text-fg">
        {c.details.map((d) => (
          <li key={d}>• {d}</li>
        ))}
      </ul>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2 text-[11.5px] text-muted">
          <Badge tone={VERDICT_TONE(c)}>{VERDICT_LABEL(c)}</Badge>
          <span>Toplam {pct(c.oos_return_pct, 2)}</span>
          {isNum(c.oos_max_dd_pct) && <span>· en büyük düşüş -%{fmtNum(c.oos_max_dd_pct, 2)}</span>}
          {isNum(c.oos_trades) && <span>· {c.oos_trades} işlem</span>}
        </div>
        <CandidateActions c={c} onDone={onDone} />
      </div>
      <p className="mt-2 text-[11px] text-subtle">{c.how}</p>
    </div>
  );
}

export function FullAnalysis({ blocked }: { blocked: boolean }) {
  const jobsMap = useApp((s) => s.backtestJobs);
  const [busy, setBusy] = useState(false);
  const now = useNow(1000);
  useAsync(
    () =>
      api.fullJobs().then((l) => {
        const st = useApp.getState();
        l.forEach((j) => st.upsertBacktestJob(j as never));
        return l;
      }),
    [],
  );
  const latest = useMemo(
    () => (Object.values(jobsMap) as unknown as FullJob[]).filter((j) => j.kind === 'full').sort((a, b) => b.created_at - a.created_at)[0],
    [jobsMap],
  );
  const full = useAsync<FullJob | undefined>(
    () => (latest && latest.status === 'done' ? api.fullJob(latest.id) : Promise.resolve(undefined)),
    [latest?.id, latest?.status],
  );
  const shown = full.data && full.data.id === latest?.id ? full.data : latest;
  const running = latest && active(latest);
  const rep = shown?.status === 'done' ? shown.result : undefined;

  const start = async () => {
    setBusy(true);
    try {
      const j = await withToast(api.runFull(), 'Tam analiz başlatılamadı');
      useApp.getState().upsertBacktestJob(j as never);
      toast.success('Tam analiz başladı — birkaç saat sürebilir, bitince Telegram’a da haber verilir');
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };

  const pctDone = Math.max(0, Math.min(100, latest?.progress.pct ?? 0));
  const elapsed = latest ? now - latest.created_at : 0;
  const eta = running && pctDone > 5 ? (elapsed / pctDone) * (100 - pctDone) : null;

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden border-accent/40">
        <CardHeader
          title="Tek tuşla tam analiz"
          subtitle="Tek butonla her şey: verileri indirir, binlerce strateji kuralını (coin grupları + coin bazlı) arar, funding arbitrajını 3 farklı şekilde test eder (Binance likit / tüm coinler / çapraz borsa), kalabalık verisini analiz eder ve hepsini hiç görülmemiş dönemdeki sonuca göre sıralayıp en iyisini sunar."
          icon={<Sparkles className="h-4 w-4 text-accent" />}
        />
        <div className="space-y-4 px-4 pb-5 pt-1">
          {running ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px]">
                <span className="flex items-center gap-2 text-fg">
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  {latest.progress.message || 'Hazırlanıyor…'}
                </span>
                <Button size="sm" variant="outline-danger" icon={<Square className="h-3.5 w-3.5" />} onClick={() => api.cancelBacktest(latest.id)}>
                  Durdur
                </Button>
              </div>
              <div>
                <div className="mb-1 flex justify-between text-[11.5px] text-subtle">
                  <span>
                    {fmtDuration(elapsed)} geçti{eta !== null ? ` · ~${fmtDuration(eta)} kaldı (tahmini)` : ''}
                  </span>
                  <span className="num font-semibold text-fg">%{Math.round(pctDone)}</span>
                </div>
                <div className="h-2.5 w-full overflow-hidden rounded-full bg-elevated">
                  <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pctDone}%` }} />
                </div>
              </div>
              <Steps steps={latest.config.steps} />
              <p className="text-[11.5px] text-subtle">
                Sayfayı kapatabilirsin; analiz sunucuda sürer. Bilgisayarı uyku moduna alma ve bot penceresini kapatma. Bitince Telegram’a mesaj gelir.
              </p>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-4">
              <div className="min-w-0 flex-1 text-[12.5px] text-muted">
                Toplam süre genelde <b className="text-fg">2–5 saat</b> (ilk seferde veri indirme dahil). Hiçbir ayar seçmene gerek yok.
              </div>
              <Button variant="primary" size="lg" icon={<Sparkles className="h-4 w-4" />} onClick={start} loading={busy} disabled={blocked}>
                Her şeyi analiz et
              </Button>
              {blocked && <p className="w-full text-right text-[11.5px] text-subtle">Çalışan başka bir iş bitince başlatabilirsin.</p>}
            </div>
          )}
        </div>
      </Card>

      {!running && shown?.status === 'failed' && <Card className="p-4 text-[12.5px] text-down">Tam analiz başarısız: {shown.error}</Card>}
      {!running && shown?.status === 'cancelled' && <Card className="p-4 text-[12.5px] text-muted">Son tam analiz durduruldu.</Card>}

      {rep && shown && (
        <Card className="overflow-hidden">
          <CardHeader
            title="Final raporu"
            subtitle={`${fmtDateTime(shown.finished_at)} · ${rep.candidates.length} aday aynı ölçüyle karşılaştırıldı: aramada hiç kullanılmamış dönemdeki yıllık getiri`}
            icon={<Trophy className="h-4 w-4 text-accent" />}
          />
          <div className="space-y-4 px-4 pb-5 pt-1">
            <div className={clsx('rounded-xl border px-4 py-3 text-[13px]', rep.best ? 'border-up/30 bg-up/[0.05] text-fg' : 'border-warn/30 bg-warn/[0.05] text-fg')}>
              {rep.summary}
            </div>
            {rep.best && <HeroCard c={rep.best} title="En iyi sonuç" tone="up" onDone={() => full.reload()} />}
            {rep.trial && <HeroCard c={rep.trial} title={rep.best ? 'İkinci en iyi aday' : 'En umut verici aday (yalnızca simülasyon)'} tone="proj" onDone={() => full.reload()} />}

            <div className="overflow-x-auto rounded-xl border border-line">
              <table className="w-full min-w-[820px] text-[12px]">
                <thead>
                  <tr className="text-[11px] text-subtle">
                    <th className="px-2 py-1.5 text-left font-medium">#</th>
                    <th className="px-2 py-1.5 text-left font-medium">Yöntem</th>
                    <th className="px-2 py-1.5 text-left font-medium">Karar</th>
                    <th className="px-2 py-1.5 text-right font-medium">Yıllık (görülmemiş dönem)</th>
                    <th className="px-2 py-1.5 text-right font-medium">Toplam</th>
                    <th className="px-2 py-1.5 text-right font-medium">En büyük düşüş</th>
                    <th className="px-2 py-1.5 text-right font-medium">İşlem</th>
                  </tr>
                </thead>
                <tbody>
                  {rep.candidates.map((c, i) => (
                    <tr key={`${c.step}-${i}`} className="border-t border-line align-top">
                      <td className="num px-2 py-1.5 text-subtle">{i + 1}</td>
                      <td className="px-2 py-1.5">
                        <div className="text-fg">{c.title}</div>
                        <div className="text-[11px] text-subtle">{c.label}</div>
                      </td>
                      <td className="px-2 py-1.5">
                        <Badge tone={VERDICT_TONE(c)}>{VERDICT_LABEL(c)}</Badge>
                      </td>
                      <td className={clsx('num px-2 py-1.5 text-right font-semibold', pnlClass(c.oos_apr_pct))}>{pct(c.oos_apr_pct)}</td>
                      <td className={clsx('num px-2 py-1.5 text-right', pnlClass(c.oos_return_pct))}>{pct(c.oos_return_pct, 2)}</td>
                      <td className="num px-2 py-1.5 text-right text-down">{isNum(c.oos_max_dd_pct) ? `-%${fmtNum(c.oos_max_dd_pct, 2)}` : '—'}</td>
                      <td className="num px-2 py-1.5 text-right">{c.oos_trades ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="rounded-xl border border-line px-3 py-2 text-[12px] text-muted">
              <b className="text-fg">Kalabalık verisi:</b>{' '}
              {rep.crowd.strong.length
                ? rep.crowd.strong.map((s) => `${s.name}: ${s.reading} (t=${fmtNum(s.diff_t, 1)})`).join(' · ')
                : rep.crowd.note || 'Henüz güçlü bir bulgu yok.'}
            </div>

            <Steps steps={shown.config.steps} />

            <ul className="space-y-1 text-[11.5px] text-subtle">
              {rep.warnings.map((w) => (
                <li key={w} className="flex gap-1.5">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  {w}
                </li>
              ))}
              <li className="flex gap-1.5">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                Her adımın ayrıntılı sonucu kendi sekmesinde (Strateji laboratuvarı, Funding arbitrajı, Kalabalık verisi) görünür.
              </li>
            </ul>
          </div>
        </Card>
      )}
    </div>
  );
}
