import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Database, Download, FlaskConical, FlaskRound, Loader2, Rocket, ShieldCheck, Square, Trophy, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { LabGroup, LabJob, LabResult, LabRow, LabStats } from '../../api/types';
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
  { value: 'hizli', label: 'Hızlı', n: 2000 },
  { value: 'normal', label: 'Normal', n: 10000 },
  { value: 'derin', label: 'Derin', n: 30000 },
  { value: 'maksimum', label: 'Maksimum', n: 100000 },
] as const;

/** Kaba süre tahmini: 30 coin · 1 yıl · 6 çekirdekte kural başına ~0,05 sn (ölçülmüş); uzun geçmişte 4 işçi. */
type LabMode = 'single' | 'groups' | 'coins';

/** Kaba süre tahmini (ölçülmüş): 30 coin · 1 yıl için kural başına ~0,25 sn tek çekirdek. */
function estimate(n: number, days: number, mode: LabMode, coins: number): string {
  const workers = days > 400 ? 4 : 6;
  const perRule30 = 0.25 * (days / 365);
  // single: n kural × 30 coin; groups: 3 × n kural × ~10 coin; coins: coins × n kural × 1 coin
  const work = mode === 'single' ? n * perRule30 : mode === 'groups' ? n * perRule30 : (coins * n * perRule30) / 30;
  const groupsCount = mode === 'single' ? 1 : mode === 'groups' ? 3 : coins;
  const sec = work / workers + 120 * groupsCount + (days > 400 ? 900 : 0);
  const lo = sec * 0.6;
  const hi = sec * 1.6;
  const f = (x: number) => (x < 3600 ? `${Math.max(1, Math.round(x / 60))} dk` : `${(x / 3600).toFixed(1).replace('.', ',')} saat`);
  return `~${f(lo)} – ${f(hi)}`;
}

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

function Stages({ b, p }: { b: LabRow | null | undefined; p: LabResult['period'] }) {
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
            {job.config.days} gün · ilk {job.config.top_n} coin · {job.config.mode === 'coins' ? `coin bazlı (${nf(job.config.candidates)} kural × ${job.config.top_n} coin)` : job.config.mode === 'groups' || job.config.grouped ? `coin grupları (${nf(job.config.candidates)} kural × 3 grup)` : `tek kural (${nf(job.config.candidates)} kural)`} · {fmtDuration(elapsed)} geçti
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
      <p className="text-[11.5px] text-subtle">Sayfayı kapatabilirsin; arama sunucuda sürer. Bilgisayarı uyku moduna alma ve bot penceresini kapatma. İlk 3 yıllık aramada veri indirme ~20-30 dk sürer (sonra önbellekten gelir).</p>
    </div>
  );
}

function normalize(r: LabResult): LabGroup[] {
  if (r.groups) return r.groups;
  return [
    {
      key: 'all', name: 'Tüm coinler', symbols: [], verdict: r.verdict, verdict_text: r.verdict_text, forward_ok: false,
      tested: r.tested, luck_bar: r.luck_bar ?? 0, min_trades: 0, shortlist: r.shortlist ?? 0, passed_oos: r.passed_oos ?? 0,
      best: r.best ?? null, finalists: r.finalists ?? [], leaderboard: r.leaderboard ?? [], full: r.full ?? null,
    },
  ];
}

function GroupView({ g, p }: { g: LabGroup; p: LabResult['period'] }) {
  const v = VERDICT[g.verdict];
  const b = g.best;
  return (
    <div className="space-y-4">
      <div className={clsx('flex flex-wrap items-start gap-3 rounded-xl border px-4 py-3', v.cls)}>
        {v.icon}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[13.5px] font-semibold text-fg">{v.title}</span>
            {b && <Badge tone="accent">{b.label}</Badge>}
            {g.verdict === 'none' && g.forward_ok && <Badge tone="proj">simülasyonda denemeye değer</Badge>}
          </div>
          <p className="mt-1 text-[12px] text-muted">{g.verdict_text}</p>
          <p className="mt-1 text-[11.5px] text-subtle">
            {nf(g.tested)} kural → {g.shortlist} güçlü aday → {g.passed_oos} doğrulamada kârlı → {g.finalists.length} finalist · şans çıtası t ≈{' '}
            {fmtNum(g.luck_bar, 2)}
          </p>
          {g.symbols.length > 0 && (
            <p className="mt-1 text-[11px] text-subtle">
              Coinler: {g.symbols.slice(0, 14).map((x) => x.replace('USDT', '')).join(', ')}
              {g.symbols.length > 14 ? ` +${g.symbols.length - 14}` : ''}
            </p>
          )}
        </div>
      </div>

      {b && (
        <div className="rounded-xl border border-line px-4 py-3">
          <div className="label mb-1.5 flex flex-wrap items-center gap-1">
            <Trophy className="h-3.5 w-3.5" /> {g.key === 'all' ? 'Bulunan kural' : `${g.name} için bulunan kural`}
            <span className={clsx('ml-2 normal-case', b.is.t_stat >= g.luck_bar ? 'text-up' : 'text-warn')}>
              eğitim t = {fmtNum(b.is.t_stat, 2)} {b.is.t_stat >= g.luck_bar ? '≥' : '<'} şans çıtası {fmtNum(g.luck_bar, 2)}
            </span>
          </div>
          <ul className="space-y-1 text-[12.5px] text-fg">
            {b.details.map((d) => (
              <li key={d}>• {d}</li>
            ))}
          </ul>
        </div>
      )}

      {b && <Stages b={b} p={p} />}

      {g.full && g.full.equity?.length > 1 && (
        <div>
          <div className="label mb-1.5 flex items-center gap-1">
            Bu kuralla tüm dönem özsermaye (gerçekçi simülasyon)
            <InfoTip>Eğitim + doğrulama + final sınavı birlikte. Karar yalnızca final sınavına göre verilir.</InfoTip>
          </div>
          <EquityDrawdownChart data={g.full.equity} initial={g.full.summary.initial_equity} height={240} />
        </div>
      )}

      <Leaderboard rows={g.leaderboard} />
    </div>
  );
}

const VERDICT_LABEL = (g: LabGroup) =>
  g.verdict === 'recommended' ? 'Önerilen' : g.verdict === 'weak' ? 'Zayıf avantaj' : g.forward_ok ? 'Simülasyonda denemeye değer' : 'Elendi';
const VERDICT_TONE = (g: LabGroup): 'up' | 'warn' | 'proj' | 'down' =>
  g.verdict === 'recommended' ? 'up' : g.verdict === 'weak' ? 'warn' : g.forward_ok ? 'proj' : 'down';

function downloadReport(groups: LabGroup[], period: LabResult['period'], job: LabJob) {
  const esc = (v: unknown) => {
    const x = v === null || v === undefined ? '' : String(v);
    return /[";\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x;
  };
  const num = (v: number | null | undefined, d = 2) => (isNum(v) ? v.toFixed(d).replace('.', ',') : '');
  const head = [
    'Coin / Grup', 'Karar', 'Strateji', 'Ayarlar', 'Tüm dönem getiri %', 'Maks. düşüş %', 'Tüm dönem işlem', 'Kazanma %',
    'Profit factor', 'Beklenti R', 'Final sınavı getiri %', 'Final işlem', 'Final beklenti R', 'Coinler',
  ];
  const rows = groups.map((g) => {
    const fs = g.full_summary;
    const f = g.best?.final;
    return [
      g.key === 'all' ? 'Tüm coinler' : g.key.endsWith('USDT') ? g.key : g.name, VERDICT_LABEL(g), g.best?.label ?? '',
      (g.best?.details ?? []).join(' | '), num(fs?.total_return_pct), num(fs?.max_drawdown_pct), fs?.trades ?? '',
      isNum(fs?.win_rate) ? num(fs!.win_rate * 100, 1) : '', num(fs?.profit_factor), num(fs?.expectancy_r),
      num(f?.return_pct), f?.trades ?? '', num(f?.expectancy_r), g.symbols.join(' '),
    ];
  });
  const meta = [
    [`Strateji Laboratuvarı raporu — ${fmtDateTime(job.finished_at)}`],
    [`Dönem: ${fmtDate(period.start)} – ${fmtDate(period.end)} · eğitim → ${fmtDate(period.split1)} · doğrulama → ${fmtDate(period.split2)} · final sınavı → ${fmtDate(period.end)}`],
    [],
  ];
  const csv = [...meta, head, ...rows].map((r) => r.map(esc).join(';')).join('\r\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `laboratuvar-raporu-${new Date(job.finished_at ?? Date.now()).toISOString().slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function ReportTable({
  groups,
  selected,
  onSelect,
  period,
  job,
}: {
  groups: LabGroup[];
  selected?: string;
  onSelect: (k: string) => void;
  period: LabResult['period'];
  job: LabJob;
}) {
  const sorted = [...groups].sort(
    (a, b) => rankOf(b) - rankOf(a) || (b.full_summary?.total_return_pct ?? -1e9) - (a.full_summary?.total_return_pct ?? -1e9),
  );
  const counts = {
    rec: groups.filter((g) => g.verdict === 'recommended').length,
    weak: groups.filter((g) => g.verdict === 'weak').length,
    fw: groups.filter((g) => g.verdict === 'none' && g.forward_ok).length,
    out: groups.filter((g) => g.verdict === 'none' && !g.forward_ok).length,
  };
  return (
    <div className="rounded-xl border border-line">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5">
        <div>
          <div className="text-[13px] font-semibold text-fg">Sonuç raporu</div>
          <div className="text-[11.5px] text-subtle">
            {counts.rec} önerilen · {counts.weak} zayıf · {counts.fw} denemeye değer · {counts.out} elendi — satıra tıklayınca ayrıntı aşağıda açılır.
            Getiriler her biri {nf(groups[0]?.full_summary?.initial_equity ?? 1000)} USDT ile başlayan ayrı simülasyonlardır.
          </div>
        </div>
        <Button size="sm" variant="secondary" onClick={() => downloadReport(groups, period, job)}>
          Raporu indir (Excel/CSV)
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-[12px]">
          <thead>
            <tr className="border-t border-line text-[11px] text-subtle">
              <th className="px-2 py-1.5 text-left font-medium">Coin / Grup</th>
              <th className="px-2 py-1.5 text-left font-medium">Karar</th>
              <th className="px-2 py-1.5 text-left font-medium">Bulunan strateji ve ayarlar</th>
              <th className="px-2 py-1.5 text-right font-medium">Tüm dönem getiri</th>
              <th className="px-2 py-1.5 text-right font-medium">Maks. düşüş</th>
              <th className="px-2 py-1.5 text-right font-medium">İşlem</th>
              <th className="px-2 py-1.5 text-right font-medium">Kazanma</th>
              <th className="px-2 py-1.5 text-right font-medium">Final sınavı</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((x) => {
              const fs = x.full_summary;
              const f = x.best?.final;
              return (
                <tr
                  key={x.key}
                  onClick={() => onSelect(x.key)}
                  className={clsx('cursor-pointer border-t border-line align-top hover:bg-elevated/60', x.key === selected && 'bg-accent/[0.06]')}
                >
                  <td className="px-2 py-2 font-medium text-fg">
                    {x.key.endsWith('USDT') ? x.key.replace('USDT', '') : x.name}
                    {!x.key.endsWith('USDT') && x.symbols.length > 0 && <div className="text-[10.5px] font-normal text-subtle">{x.symbols.length} coin</div>}
                  </td>
                  <td className="px-2 py-2">
                    <Badge tone={VERDICT_TONE(x)}>{VERDICT_LABEL(x)}</Badge>
                  </td>
                  <td className="px-2 py-2">
                    <div className="text-fg">{x.best?.label ?? 'Kural bulunamadı'}</div>
                    {x.best && <div className="mt-0.5 text-[10.5px] leading-snug text-subtle">{x.best.details.slice(1).join(' · ')}</div>}
                  </td>
                  <td className={clsx('num px-2 py-2 text-right font-semibold', pnlClass(fs?.total_return_pct ?? 0))}>{pctU(fs?.total_return_pct)}</td>
                  <td className="num px-2 py-2 text-right text-down">{isNum(fs?.max_drawdown_pct) ? `-${fmtNum(fs!.max_drawdown_pct, 1)}%` : '—'}</td>
                  <td className="num px-2 py-2 text-right">{fs?.trades ?? '—'}</td>
                  <td className="num px-2 py-2 text-right">{isNum(fs?.win_rate) ? fmtPct(fs!.win_rate, { digits: 0 }) : '—'}</td>
                  <td className={clsx('num px-2 py-2 text-right', pnlClass(f?.return_pct ?? 0))}>
                    {pctU(f?.return_pct)}
                    <div className="text-[10.5px] text-subtle">{f ? `${f.trades} işlem · ${fmtR(f.expectancy_r)}` : ''}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const rankOf = (g: LabGroup) => (g.verdict === 'recommended' ? 3 : g.verdict === 'weak' ? 2 : g.forward_ok ? 1 : 0);

function ResultPanel({
  job,
  onApply,
  applying,
}: {
  job: LabJob;
  onApply: (mode: 'live' | 'paper') => void;
  applying: 'live' | 'paper' | null;
}) {
  const r = job.result!;
  const groups = normalize(r);
  const [tab, setTab] = useState<string>(groups[0]?.key ?? 'all');
  const g = groups.find((x) => x.key === tab) ?? groups[0];
  const v = VERDICT[r.verdict];
  const canLive = groups.some((x) => x.best && x.verdict !== 'none');
  const canPaper = groups.some((x) => x.best && (x.forward_ok || x.verdict !== 'none'));
  return (
    <div className="space-y-4">
      <div className={clsx('flex flex-wrap items-start gap-3 rounded-xl border px-4 py-3', canLive ? v.cls : canPaper ? 'border-proj/30 bg-proj/[0.06]' : v.cls)}>
        {canLive ? v.icon : canPaper ? <FlaskRound className="h-5 w-5 text-proj" /> : v.icon}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-fg">{canLive ? v.title : canPaper ? 'Umut verici aday var (kanıt yetersiz)' : v.title}</span>
            {job.applied_at && (
              <Badge tone="up">
                <ShieldCheck className="mr-1 inline h-3 w-3" />
                {job.applied_mode === 'paper' ? 'Simülasyonda deneniyor' : 'Uygulandı'}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-[12.5px] text-muted">{r.verdict_text}</p>
          <p className="mt-1 text-[11.5px] text-subtle">
            Toplam {nf(r.tested)} kural · {Math.round((r.period.end - r.period.start) / 86_400_000)} günlük geçmiş · {fmtDateTime(job.finished_at)}
          </p>
        </div>
        <div className="flex flex-col gap-2">
          {canLive && (
            <Button variant="success" icon={<Rocket className="h-4 w-4" />} onClick={() => onApply('live')} loading={applying === 'live'}>
              Doğrulanan kuralları uygula
            </Button>
          )}
          {canPaper && (
            <Button variant="secondary" icon={<FlaskRound className="h-4 w-4" />} onClick={() => onApply('paper')} loading={applying === 'paper'}>
              Simülasyonda canlı dene
            </Button>
          )}
        </div>
      </div>

      <ReportTable groups={groups} selected={g?.key} onSelect={setTab} period={r.period} job={job} />
      {g && <GroupView g={g} p={r.period} />}

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
  const [days, setDays] = useState('1095');
  const [budget, setBudget] = useState<string>('derin');
  const [mode, setMode] = useState<LabMode>('groups');
  const [coins, setCoins] = useState('15');
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [applying, setApplying] = useState<'live' | 'paper' | null>(null);
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
      const j = await withToast(api.runLab({ days: Number(days), top_n: mode === 'coins' ? Number(coins) : 30, budget, mode }), 'Laboratuvar başlatılamadı');
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

  const apply = async (mode: 'live' | 'paper') => {
    const r = shown?.result;
    if (!shown || !r) return;
    const groups = normalize(r).filter((g) => g.best && (mode === 'live' ? g.verdict !== 'none' : g.forward_ok || g.verdict !== 'none'));
    const ok = await confirm({
      title: mode === 'live' ? 'Doğrulanan kurallar uygulansın mı?' : 'Kurallar Simülasyon’da canlı denensin mi?',
      message:
        mode === 'live'
          ? 'Doğrulanan, bu kuralların TEK BAŞINA çalışmasıdır: S1–S5 kapatılır ve canlı tarama her coin için kendi grubunun kuralıyla fırsat arar.'
          : 'Kurallar DENEME olarak uygulanır: fırsatlar görünür ama otomatik işlem yalnızca Simülasyon ortamında (sanal parayla) açılır. Birkaç hafta sonra Geçmiş sayfasında gerçek sonuçları görürsün. S1–S5 kapatılır ki sonuç yalnızca bu kuralları ölçsün.',
      details: (
        <ul className="space-y-0.5 text-[12px]">
          {groups.map((g) => (
            <li key={g.key}>
              • <b>{g.name}:</b> {g.best!.label}
            </li>
          ))}
        </ul>
      ),
      confirmText: mode === 'live' ? 'Uygula' : 'Simülasyonda başlat',
      tone: 'success',
    });
    if (!ok) return;
    setApplying(mode);
    try {
      await withToast(api.applyLab(shown.id, mode), 'Kural uygulanamadı');
      toast.success(mode === 'live' ? 'Kurallar uygulandı — tarama artık bu kurallarla çalışıyor' : 'Simülasyonda canlı deneme başladı');
      full.reload();
      onApplied();
    } catch {
      /* toast */
    } finally {
      setApplying(null);
    }
  };

  const running = latest && active(latest);
  const dataJob = useMemo(
    () => Object.values(jobsMap).filter((j) => j.kind === 'data').sort((a, b) => b.created_at - a.created_at)[0],
    [jobsMap],
  );
  const dataRunning = !!dataJob && active(dataJob);
  const dstat = useAsync(() => api.dataStatus(), [dataJob?.status]);
  const [syncing, setSyncing] = useState(false);
  const dataDays = Math.round(((dstat.data?.last ?? 0) - (dstat.data?.first ?? 0)) / 86_400_000);
  const needDays = Number(days);
  const covered = !!dstat.data?.synthetic || (!!dstat.data?.symbols && dataDays >= needDays - 2);
  const stale = !!dstat.data?.last && Date.now() - dstat.data.last > 2 * 86_400_000;
  const sync = async () => {
    setSyncing(true);
    try {
      const j = await withToast(api.dataSync({ days: needDays, top_n: mode === 'coins' ? Math.max(Number(coins), 30) : 30 }), 'Veri indirme başlatılamadı');
      useApp.getState().upsertBacktestJob(j);
      toast.success('Veriler indiriliyor — bitince strateji aramasını başlatabilirsin');
    } catch {
      /* toast */
    } finally {
      setSyncing(false);
    }
  };

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
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line px-3 py-2.5">
          <Database className="h-4 w-4 text-muted" />
          <div className="min-w-0 flex-1 text-[12px]">
            <div className="font-medium text-fg">1 · Geçmiş veri</div>
            {dataRunning ? (
              <div className="text-muted">
                <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
                {dataJob!.progress.message} · %{Math.round(dataJob!.progress.pct)}
              </div>
            ) : dstat.data?.synthetic ? (
              <div className="text-subtle">Sentetik geliştirme verisi (indirme gerekmez)</div>
            ) : dstat.data?.symbols ? (
              <div className={clsx(covered && !stale ? 'text-subtle' : 'text-warn')}>
                Önbellekte {dstat.data.symbols} coin · {fmtDate(dstat.data.first)} – {fmtDate(dstat.data.last)} ({dataDays} gün) ·{' '}
                {fmtNum(dstat.data.size_mb, 0)} MB · son güncelleme {fmtDateTime(dstat.data.updated_at)}
                {!covered && ` — seçilen ${needDays} gün için yetersiz, önce indir`}
                {covered && stale && ' — son günler eksik, istersen güncelle'}
              </div>
            ) : (
              <div className="text-warn">Henüz veri indirilmedi. Önce verileri indir (3 yıl · 30 coin ilk seferde ~20-30 dk; sonra saniyeler).</div>
            )}
          </div>
          {!dstat.data?.synthetic && (
            dataRunning ? (
              <Button size="sm" variant="outline-danger" icon={<Square className="h-3.5 w-3.5" />} onClick={() => api.cancelBacktest(dataJob!.id)}>
                Durdur
              </Button>
            ) : (
              <Button size="sm" variant={covered ? 'secondary' : 'primary'} icon={<Download className="h-3.5 w-3.5" />} onClick={sync} loading={syncing} disabled={!!running}>
                {dstat.data?.symbols ? 'Verileri güncelle' : 'Verileri indir'}
              </Button>
            )
          )}
        </div>
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
                  { value: '365', label: '1 yıl' },
                  { value: '730', label: '2 yıl' },
                  { value: '1095', label: '3 yıl' },
                ]}
              />
            </div>
            <div>
              <div className="label mb-1 flex items-center gap-1">
                Yöntem
                <InfoTip>
                  Coin grupları: coinler eğitim dönemindeki hacim ve oynaklıklarına göre 3 gruba ayrılır ve her gruba ayrı kural aranır.
                  Coin bazlı: her coine ayrı ayrı binlerce kural denenir; tek coinde veri az olduğu için koruma kuralları her coin için ayrı
                  uygulanır ve çoğu coinin elenmesi normaldir. Tek kural: tüm coinlere ortak tek kural.
                </InfoTip>
              </div>
              <Segmented
                value={mode}
                onChange={setMode}
                options={[
                  { value: 'groups', label: 'Coin grupları' },
                  { value: 'coins', label: 'Coin bazlı' },
                  { value: 'single', label: 'Tek kural' },
                ]}
              />
            </div>
            {mode === 'coins' && (
              <div>
                <div className="label mb-1">Coin sayısı</div>
                <Segmented
                  value={coins}
                  onChange={setCoins}
                  options={[
                    { value: '5', label: 'İlk 5' },
                    { value: '10', label: 'İlk 10' },
                    { value: '15', label: 'İlk 15' },
                    { value: '30', label: 'İlk 30' },
                  ]}
                />
              </div>
            )}
            <div>
              <div className="label mb-1 flex items-center gap-1">
                Kural sayısı{' '}
                <span className="normal-case text-subtle">
                  ({sel ? `${nf(sel.n)} kural${mode === 'groups' ? ' × 3 grup' : mode === 'coins' ? ` × ${coins} coin` : ''} · ${estimate(sel.n, Number(days), mode, Number(coins))}` : ''})
                </span>
              </div>
              <Segmented value={budget} onChange={setBudget} options={BUDGETS.map((b) => ({ value: b.value, label: b.label }))} />
            </div>
            <Button
              variant="primary"
              size="lg"
              icon={<FlaskConical className="h-4 w-4" />}
              onClick={start}
              loading={busy}
              disabled={blocked || dataRunning}
              className="ml-auto"
              title="Önbellekteki veriyle arar; ağdan yalnızca eksik coin/dönem indirilir"
            >
              2 · En iyi stratejiyi bul
            </Button>
            {blocked && <p className="w-full text-right text-[11.5px] text-subtle">Çalışan başka bir iş bitince başlatabilirsin.</p>}
          </div>
        )}

        {!running && shown?.status === 'done' && shown.result && <ResultPanel key={shown.id} job={shown} onApply={apply} applying={applying} />}
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
