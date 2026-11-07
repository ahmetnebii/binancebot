import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Circle, FlaskRound, Loader2, Rocket, Sparkles, Square, Trophy, XCircle } from 'lucide-react';
import { Fragment, useMemo, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { FullCandidate, FullJob, FullStep } from '../../api/types';
import { useAsync, useNow } from '../../lib/hooks';
import { useSettingsCache } from '../trade/useRiskBounds';
import { fmtDateTime, fmtDuration, fmtNum, pnlClass } from '../../lib/format';
import { toast, useApp } from '../../store/app';
import { InfoTip, useConfirm } from '../ui/overlay';
import { Badge, Button, Card, CardHeader, Segmented } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const pct = (v: number | null | undefined, d = 1) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '-' : ''}%${Math.abs(v).toFixed(d).replace('.', ',')}` : '—');
const active = (j: { status: string }) => j.status === 'queued' || j.status === 'running';

const VERDICT_LABEL = (c: FullCandidate) =>
  c.verdict === 'recommended' ? 'Önerilen' : c.verdict === 'weak' ? 'Zayıf avantaj' : c.forward_ok ? 'Denemeye değer' : 'Elendi';
const VERDICT_TONE = (c: FullCandidate): 'up' | 'warn' | 'proj' | 'down' =>
  c.verdict === 'recommended' ? 'up' : c.verdict === 'weak' ? 'warn' : c.forward_ok ? 'proj' : 'down';

/* --- kaldıraç senaryosu ------------------------------------------------------- */
const BORROW_APR = 8; // funding'i büyütmek için ödünç alınan USDT'nin yıllık maliyeti (varsayım)
type Lev = '1' | '2' | '3' | '5';

interface Scaled {
  monthly: { month: string; return_pct: number }[];
  apr: number;
  dd: number | null;
  avg: number | null;
  best: number | null;
  worst: number | null;
  pos: number;
  n: number;
  danger: boolean;
}

function scale(c: FullCandidate, k: number): Scaled {
  const m = (c.monthly ?? []).map((x) => ({
    month: x.month,
    return_pct: c.kind === 'carry' ? k * x.return_pct - ((k - 1) * BORROW_APR) / 12 : k * x.return_pct,
  }));
  const vals = m.map((x) => x.return_pct);
  const apr = c.kind === 'carry' ? k * c.oos_apr_pct - (k - 1) * BORROW_APR : k * c.oos_apr_pct;
  const dd = isNum(c.oos_max_dd_pct) ? c.oos_max_dd_pct * k : null;
  const worst = vals.length ? Math.min(...vals) : null;
  return {
    monthly: m,
    apr,
    dd,
    avg: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null,
    best: vals.length ? Math.max(...vals) : null,
    worst,
    pos: vals.filter((v) => v > 0).length,
    n: vals.length,
    danger: (dd ?? 0) > 30 || (worst ?? 0) < -20 || (c.kind === 'carry' && k >= 5),
  };
}

function MonthlyChips({ m }: { m: Scaled['monthly'] }) {
  if (!m.length) return <span className="text-[11.5px] text-subtle">Aylık veri yok (bu sonuç eski bir analizden olabilir).</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {m.map((x) => (
        <div key={x.month} className={clsx('rounded-lg border px-2 py-1 text-[11px]', x.return_pct >= 0 ? 'border-up/30 bg-up/[0.05]' : 'border-down/30 bg-down/[0.05]')}>
          <div className="text-subtle">{x.month}</div>
          <div className={clsx('num font-semibold', pnlClass(x.return_pct))}>{pct(x.return_pct, 2)}</div>
        </div>
      ))}
    </div>
  );
}

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

function HeroCard({ c, title, tone, onDone, k }: { c: FullCandidate; title: string; tone: 'up' | 'proj'; onDone: () => void; k: number }) {
  const sc = scale(c, k);
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
          <div className={clsx('num text-[26px] font-bold', pnlClass(sc.avg ?? sc.apr))}>{sc.avg !== null ? `${pct(sc.avg, 2)} / ay` : pct(sc.apr)}</div>
          <div className="text-[11px] text-subtle">
            ortalama aylık · yıllık {pct(sc.apr)} · {k}x · hiç görülmemiş {Math.round(c.oos_days)} gün
          </div>
        </div>
      </div>
      <ul className="mt-3 space-y-0.5 text-[12px] text-fg">
        {c.details.map((d) => (
          <li key={d}>• {d}</li>
        ))}
      </ul>
      <div className="mt-3">
        <div className="label mb-1.5">Aylık getiriler ({k}x, aramada hiç kullanılmamış dönem)</div>
        <MonthlyChips m={sc.monthly} />
      </div>
      {sc.danger && (
        <div className="mt-3 flex gap-2 rounded-lg border border-down/30 bg-down/[0.06] px-3 py-2 text-[12px] text-down">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Bu kaldıraçta risk çok yüksek: kötü bir dönem hesabın büyük kısmını eritebilir{c.kind === 'carry' ? ' ve ani fiyat hareketinde short bacak likide olabilir' : ''}.
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2 text-[11.5px] text-muted">
          <Badge tone={VERDICT_TONE(c)}>{VERDICT_LABEL(c)}</Badge>
          {sc.best !== null && <span>En iyi ay {pct(sc.best, 2)}</span>}
          {sc.worst !== null && <span>· en kötü ay {pct(sc.worst, 2)}</span>}
          {sc.n > 0 && <span>· {sc.pos}/{sc.n} ay artı</span>}
          {sc.dd !== null && <span>· en büyük düşüş -%{fmtNum(sc.dd, 1)}</span>}
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
  const [lev, setLev] = useState<Lev>('3');
  const [open, setOpen] = useState<number | null>(null);
  const settings = useSettingsCache();
  const baseRisk = settings?.risk?.base_risk_pct;
  const k = Number(lev);
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
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2.5">
              <div className="text-[12.5px] font-medium text-fg">Kaldıraç</div>
              <Segmented
                value={lev}
                onChange={setLev}
                options={[
                  { value: '1', label: '1x' },
                  { value: '2', label: '2x' },
                  { value: '3', label: '3x' },
                  { value: '5', label: '5x' },
                ]}
              />
              <InfoTip>
                Strateji kurallarında: işlem başına risk ×{k} (1x = mevcut risk ayarın{isNum(baseRisk) ? `, işlem başına %${fmtNum(baseRisk * 100, 2)}` : ''}; aynı
                stopla pozisyon {k} kat büyür). Funding arbitrajında: pozisyon büyüklüğü ×{k}; fazlası ödünç USDT ile, yıllık %{BORROW_APR} faiz maliyeti
                düşülür. Getiri de düşüş de yaklaşık {k} kat büyür.
              </InfoTip>
              <span className="text-[11.5px] text-subtle">Tüm sonuçlar seçilen kaldıraca göre yeniden hesaplanır (yaklaşık).</span>
            </div>
            {rep.best && <HeroCard c={rep.best} title="En iyi sonuç" tone="up" onDone={() => full.reload()} k={k} />}
            {rep.trial && <HeroCard c={rep.trial} title={rep.best ? 'İkinci en iyi aday' : 'En umut verici aday (yalnızca simülasyon)'} tone="proj" onDone={() => full.reload()} k={k} />}

            <div className="overflow-x-auto rounded-xl border border-line">
              <table className="w-full min-w-[900px] text-[12px]">
                <thead>
                  <tr className="text-[11px] text-subtle">
                    <th className="px-2 py-1.5 text-left font-medium">#</th>
                    <th className="px-2 py-1.5 text-left font-medium">Yöntem</th>
                    <th className="px-2 py-1.5 text-left font-medium">Karar</th>
                    <th className="px-2 py-1.5 text-right font-medium">Aylık ort.</th>
                    <th className="px-2 py-1.5 text-right font-medium">En iyi ay</th>
                    <th className="px-2 py-1.5 text-right font-medium">En kötü ay</th>
                    <th className="px-2 py-1.5 text-right font-medium">Artı ay</th>
                    <th className="px-2 py-1.5 text-right font-medium">Yıllık</th>
                    <th className="px-2 py-1.5 text-right font-medium">En büyük düşüş</th>
                  </tr>
                </thead>
                <tbody>
                  {rep.candidates.map((c, i) => {
                    const sc = scale(c, k);
                    return (
                      <Fragment key={`${c.step}-${i}`}>
                        <tr
                          onClick={() => setOpen(open === i ? null : i)}
                          className={clsx('cursor-pointer border-t border-line align-top hover:bg-elevated/60', open === i && 'bg-accent/[0.05]')}
                        >
                          <td className="num px-2 py-1.5 text-subtle">{i + 1}</td>
                          <td className="px-2 py-1.5">
                            <div className="text-fg">{c.title}</div>
                            <div className="text-[11px] text-subtle">{c.label}</div>
                          </td>
                          <td className="px-2 py-1.5">
                            <Badge tone={VERDICT_TONE(c)}>{VERDICT_LABEL(c)}</Badge>
                          </td>
                          <td className={clsx('num px-2 py-1.5 text-right font-semibold', pnlClass(sc.avg ?? 0))}>{pct(sc.avg, 2)}</td>
                          <td className={clsx('num px-2 py-1.5 text-right', pnlClass(sc.best ?? 0))}>{pct(sc.best, 2)}</td>
                          <td className={clsx('num px-2 py-1.5 text-right', pnlClass(sc.worst ?? 0))}>{pct(sc.worst, 2)}</td>
                          <td className="num px-2 py-1.5 text-right">{sc.n ? `${sc.pos}/${sc.n}` : '—'}</td>
                          <td className={clsx('num px-2 py-1.5 text-right', pnlClass(sc.apr))}>{pct(sc.apr)}</td>
                          <td className={clsx('num px-2 py-1.5 text-right', sc.danger ? 'font-semibold text-down' : 'text-down')}>
                            {sc.dd !== null ? `-%${fmtNum(sc.dd, 1)}` : '—'}
                          </td>
                        </tr>
                        {open === i && (
                          <tr className="border-t border-line bg-elevated/30">
                            <td />
                            <td colSpan={8} className="px-2 py-2">
                              <MonthlyChips m={sc.monthly} />
                              <div className="mt-2">
                                <CandidateActions c={c} onDone={() => full.reload()} />
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-[11.5px] text-subtle">Satıra tıklayınca o yöntemin her ayı ayrı ayrı açılır.</p>

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
