import clsx from 'clsx';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  BarChart3,
  CalendarRange,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Dices,
  FlaskConical,
  Gauge,
  History as HistoryIcon,
  ListOrdered,
  Loader2,
  Play,
  RotateCcw,
  Scale,
  Sparkles,
  Square,
  Trash2,
  XCircle,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, withToast } from '../api/client';
import type {
  BacktestConfigIn,
  BacktestJob,
  BacktestPhase,
  BacktestResult,
  BacktestStatus,
  BacktestTrade,
  Calibration,
  MarketSymbol,
} from '../api/types';
import { AutoOptimize } from '../components/backtest/AutoOptimize';
import { EquityDrawdownChart } from '../components/analytics/EquityDrawdownChart';
import { MonthlyHeatmap } from '../components/analytics/MonthlyHeatmap';
import { RHistogram } from '../components/analytics/RHistogram';
import { strategyTone } from '../components/trade/helpers';
import { useSettingsCache } from '../components/trade/useRiskBounds';
import { ChipsInput, Field, NumberInput, Slider } from '../components/ui/form';
import { InfoTip, Tooltip, useConfirm } from '../components/ui/overlay';
import { Badge, Button, Card, CardHeader, Empty, Segmented, SideBadge, Skeleton, Switch } from '../components/ui/primitives';
import { useAsync, useInterval, useNow } from '../lib/hooks';
import { fmtDate, fmtDateTime, fmtDuration, fmtNum, fmtPct, fmtPrice, fmtR, fmtUsd, pnlClass } from '../lib/format';
import { navigate } from '../lib/router';
import { toast, useApp } from '../store/app';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);

const dtfDayMonth = new Intl.DateTimeFormat('tr-TR', { day: 'numeric', month: 'short' });

/** "4 Eki – 3 Kas 2026"; yıllar farklıysa iki tarih de yıl içerir. */
function fmtDateRange(a: number | null | undefined, b: number | null | undefined): string {
  if (!isNum(a) || !isNum(b)) return `${fmtDate(a)} – ${fmtDate(b)}`;
  return new Date(a).getFullYear() === new Date(b).getFullYear() ? `${dtfDayMonth.format(a)} – ${fmtDate(b)}` : `${fmtDate(a)} – ${fmtDate(b)}`;
}
/** Yüzde birimli değer (2.5 = %2,5) → biçimli metin. */
const pctU = (v: number | null | undefined, sign = true, digits = 2) => (isNum(v) ? fmtPct(v / 100, { sign, digits }) : '—');

const PHASES: { key: Exclude<BacktestPhase, 'done'>; label: string; desc: string }[] = [
  { key: 'data', label: 'Veri indiriliyor', desc: 'Geçmiş mumlar ve funding verisi' },
  { key: 'simulate', label: 'Simülasyon', desc: 'Strateji + risk motoru bar bar' },
  { key: 'analyze', label: 'Analiz', desc: 'Metrikler, Monte Carlo, walk-forward' },
];

const STATUS_META: Record<BacktestStatus, { label: string; tone: 'neutral' | 'accent' | 'up' | 'down' | 'warn' }> = {
  queued: { label: 'Sırada', tone: 'neutral' },
  running: { label: 'Çalışıyor', tone: 'accent' },
  done: { label: 'Tamamlandı', tone: 'up' },
  failed: { label: 'Hata', tone: 'down' },
  cancelled: { label: 'İptal edildi', tone: 'warn' },
};

const STRAT_LABEL: Record<string, string> = { s1: 'S1 · Trend Kırılımı', s2: 'S2 · Göreli Güç', s3: 'S3 · Ortalamaya Dönüş', s4: 'S4 · 4s Trend Takibi', s5: 'S5 · Grafik Formasyonu' };
const isActive = (j: BacktestJob) => j.status === 'queued' || j.status === 'running';

function strategiesText(st: BacktestJob['config']['strategies'] | undefined): string {
  if (!st) return '—';
  const on = (['s1', 's2', 's3', 's4', 's5'] as const).filter((k) => st[k]).map((k) => k.toUpperCase());
  return on.length ? on.join(' + ') : '—';
}

function universeText(cfg: BacktestJob['config']): string {
  if (cfg.symbols && cfg.symbols.length) return cfg.symbols.length <= 3 ? cfg.symbols.join(', ') : `${cfg.symbols.length} sembol`;
  return `ilk ${cfg.top_n}`;
}

/* ------------------------------------------------------------------ */
/* Yapılandırma                                                         */
/* ------------------------------------------------------------------ */

const DAY_PRESETS = ['30', '60', '90', '180', '365'] as const;

function ConfigCard({ blocked, onStarted }: { blocked: BacktestJob | undefined; onStarted: (j: BacktestJob) => void }) {
  const settings = useSettingsCache();
  const [days, setDays] = useState<number>(90);
  const [topN, setTopN] = useState<number>(25);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [strat, setStrat] = useState<{ s1: boolean; s2: boolean; s3: boolean; s4: boolean; s5: boolean }>({ s1: true, s2: true, s3: false, s4: false, s5: false });
  const [equity, setEquity] = useState<number | null>(1000);
  const [slip, setSlip] = useState<number | null>(3);
  const [taker, setTaker] = useState<number | null>(0.0005);
  const [maker, setMaker] = useState<number | null>(0.0002);
  const [adv, setAdv] = useState(false);
  const [busy, setBusy] = useState(false);
  const inited = useRef(false);
  const symList = useAsync<MarketSymbol[]>(() => api.symbols(), []);

  useEffect(() => {
    if (!settings || inited.current) return;
    inited.current = true;
    setStrat({ s1: settings.strategies.s1_enabled, s2: settings.strategies.s2_enabled, s3: settings.strategies.s3_enabled ?? false, s4: settings.strategies.s4_enabled ?? false, s5: settings.strategies.s5_enabled ?? false });
    setEquity(settings.paper.starting_balance || 1000);
    setSlip(settings.paper.slippage_bps);
    setTaker(settings.paper.taker_fee);
    setMaker(settings.paper.maker_fee);
  }, [settings]);

  const popular = useMemo(
    () => [...(symList.data ?? [])].sort((a, b) => b.quote_volume - a.quote_volume).slice(0, 10).map((s) => s.symbol),
    [symList.data],
  );
  const known = useMemo(() => new Set((symList.data ?? []).map((s) => s.symbol)), [symList.data]);
  const unknown = symbols.filter((s) => known.size > 0 && !known.has(s));
  const noStrategy = !strat.s1 && !strat.s2 && !strat.s3 && !strat.s4 && !strat.s5;
  const dayPreset = (DAY_PRESETS as readonly string[]).includes(String(days)) ? String(days) : 'custom';

  const run = async () => {
    if (noStrategy) return;
    const cfg: BacktestConfigIn = {
      days: Math.round(Math.max(7, Math.min(365, days))),
      top_n: Math.round(Math.max(5, Math.min(80, topN))),
      strategies: { ...strat },
      initial_equity: equity ?? 1000,
    };
    if (symbols.length) cfg.symbols = symbols;
    if (slip !== null) cfg.slippage_bps = slip;
    if (taker !== null) cfg.taker_fee = taker;
    if (maker !== null) cfg.maker_fee = maker;
    setBusy(true);
    try {
      const job = await withToast(api.runBacktest(cfg), 'Backtest başlatılamadı');
      useApp.getState().upsertBacktestJob(job);
      toast.info('Backtest başlatıldı', `${cfg.days} gün · ${symbols.length ? `${symbols.length} sembol` : `ilk ${cfg.top_n} sembol`}`);
      onStarted(job);
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader
        title="Test yapılandırması"
        subtitle="Stratejiler geçmiş veride canlıdaki aynı kurallarla çalıştırılır"
        icon={<FlaskConical className="h-4 w-4" />}
      />
      <div className="space-y-5 px-4 pb-4 pt-1">
        <div className="grid gap-5 lg:grid-cols-2">
          <Field label="Dönem" hint="Geçmişe doğru kaç günlük veri kullanılacağı. Uzun dönem daha güvenilir ama yavaştır.">
            <div className="flex flex-wrap items-center gap-2">
              <Segmented
                value={dayPreset as (typeof DAY_PRESETS)[number] | 'custom'}
                onChange={(v) => v !== 'custom' && setDays(Number(v))}
                options={[
                  ...DAY_PRESETS.map((d) => ({ value: d, label: `${d} g` })),
                  ...(dayPreset === 'custom' ? [{ value: 'custom' as const, label: 'Özel' }] : []),
                ]}
              />
              <div className="w-[120px]">
                <NumberInput value={days} onChange={(v) => v !== null && setDays(Math.round(v))} min={7} max={365} decimals={0} suffix="gün" />
              </div>
            </div>
          </Field>
          <Field
            label={
              <span className="flex items-center gap-1">
                Sembol evreni <InfoTip>Sembol seçmezsen 24 saatlik hacme göre en büyük N sembol kullanılır. BTCUSDT rejim için her zaman dahildir.</InfoTip>
              </span>
            }
            right={<span className="num text-[12px] font-semibold text-fg">{symbols.length ? `${symbols.length} seçili` : `ilk ${topN}`}</span>}
          >
            <Slider value={topN} min={5} max={80} step={1} onChange={(v) => setTopN(Math.round(v))} className={symbols.length ? 'opacity-40' : undefined} />
            <div className="num mt-0.5 flex justify-between text-[10.5px] text-subtle">
              <span>5</span>
              <span>hacme göre en büyük {topN}</span>
              <span>80</span>
            </div>
          </Field>
        </div>

        <Field
          label="Belirli semboller (isteğe bağlı)"
          hint={unknown.length ? undefined : 'Boş bırakılırsa yukarıdaki evren kullanılır. Enter veya virgülle ekleyin.'}
          error={unknown.length ? `Bilinmeyen sembol: ${unknown.join(', ')}` : undefined}
        >
          <ChipsInput value={symbols} onChange={setSymbols} placeholder="ör. BTCUSDT, ETHUSDT" />
          {popular.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-subtle">Hızlı ekle:</span>
              {popular
                .filter((p) => !symbols.includes(p))
                .slice(0, 8)
                .map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setSymbols([...symbols, p])}
                    className="num rounded-md border border-line bg-elevated px-2 py-0.5 text-[11px] text-muted transition-colors hover:border-line-strong hover:text-fg"
                  >
                    + {p}
                  </button>
                ))}
            </div>
          )}
        </Field>

        <div>
          <div className="mb-1.5 text-[12px] font-medium text-muted">Stratejiler</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
            {(['s1', 's2', 's3', 's4', 's5'] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setStrat({ ...strat, [k]: !strat[k] })}
                className={clsx(
                  'flex items-center justify-between gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors',
                  strat[k] ? 'border-accent/40 bg-accent/[0.06]' : 'border-line hover:border-line-strong',
                )}
              >
                <span className="min-w-0">
                  <span className="block text-[12.5px] font-medium text-fg">{STRAT_LABEL[k]}</span>
                  <span className="block text-[11px] text-subtle">{k === 's1' ? '15 dk kırılım' : k === 's2' ? '1 sa rotasyon' : k === 's3' ? 'yatay piyasada dönüş' : k === 's4' ? '4 sa trend, az işlem' : 'OBO, ikili tepe/dip, üçgen'}</span>
                </span>
                <Switch checked={strat[k]} onChange={(v) => setStrat({ ...strat, [k]: v })} size="sm" />
              </button>
            ))}
          </div>
          {noStrategy && <div className="mt-1.5 text-[11.5px] text-down">En az bir strateji seçin.</div>}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Başlangıç sermayesi">
            <NumberInput value={equity} onChange={setEquity} min={10} max={10_000_000} decimals={2} suffix="USDT" />
          </Field>
          <div>
            <button
              type="button"
              onClick={() => setAdv((v) => !v)}
              className="mt-6 flex h-9 w-full items-center justify-between rounded-lg border border-line px-3 text-[12.5px] text-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Gelişmiş: kayma ve komisyon
              <ChevronDown className={clsx('h-4 w-4 transition-transform', adv && 'rotate-180')} />
            </button>
          </div>
        </div>
        {adv && (
          <div className="grid gap-4 rounded-xl border border-line bg-bg/40 p-3 sm:grid-cols-3">
            <Field label="Kayma" hint="Piyasa emirlerinde varsayılan kayma">
              <NumberInput value={slip} onChange={setSlip} min={0} max={100} decimals={1} suffix="bps" />
            </Field>
            <Field label="Taker komisyonu">
              <NumberInput value={taker} onChange={setTaker} scale={100} min={0} max={0.01} decimals={3} suffix="%" />
            </Field>
            <Field label="Maker komisyonu">
              <NumberInput value={maker} onChange={setMaker} scale={100} min={0} max={0.01} decimals={3} suffix="%" />
            </Field>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-muted">
            Mevcut risk ve strateji ayarlarınla çalışır. Sonuçlar geçmişe aittir, geleceği garanti etmez.
          </p>
          {blocked ? (
            <Button
              variant="secondary"
              onClick={() => (blocked.kind === 'optimize' ? window.scrollTo({ top: 0, behavior: 'smooth' }) : navigate(`/backtest/${blocked.id}`))}
              icon={<Loader2 className="h-4 w-4 animate-spin" />}
            >
              {blocked.kind === 'optimize' ? 'Ayar arama çalışıyor' : 'Bir test çalışıyor — izle'}
            </Button>
          ) : (
            <Button variant="primary" size="lg" icon={<Play className="h-4 w-4" />} onClick={run} loading={busy} disabled={noStrategy || unknown.length > 0}>
              Testi başlat
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Kalibrasyon ve geçmiş testler                                        */
/* ------------------------------------------------------------------ */

function CalibrationCard({ cal, loading, onReset }: { cal: Calibration | undefined; loading: boolean; onReset: () => void }) {
  const rows = Object.entries(cal?.strategies ?? {});
  return (
    <Card>
      <CardHeader
        title="EV kalibrasyonu"
        subtitle={cal?.applied_at ? `${fmtDateTime(cal.applied_at)} uygulandı` : 'Varsayılan istatistikler'}
        icon={<Scale className="h-4 w-4" />}
        right={
          rows.length ? (
            <Button size="xs" variant="ghost" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={onReset}>
              Varsayılana dön
            </Button>
          ) : undefined
        }
      />
      <div className="px-4 pb-4">
        {loading && !cal ? (
          <Skeleton className="h-16 w-full" />
        ) : rows.length ? (
          <>
            <table className="w-full text-[12px]">
              <thead className="table-head border-y border-line">
                <tr>
                  <th className="!px-2">Strateji</th>
                  <th className="!px-2 !text-right">n</th>
                  <th className="!px-2 !text-right">Kazanma</th>
                  <th className="!px-2 !text-right">Ort. kazanç</th>
                  <th className="!px-2 !text-right">Beklenti</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([k, v]) => (
                  <tr key={k} className="border-b border-line/60">
                    <td className="px-2 py-2">
                      <Badge tone={strategyTone(k)}>{k}</Badge>
                    </td>
                    <td className="num px-2 py-2 text-right text-muted">{v.n}</td>
                    <td className="num px-2 py-2 text-right text-fg">{fmtPct(v.win_rate, { digits: 0 })}</td>
                    <td className="num px-2 py-2 text-right text-up">{fmtR(v.avg_win_r, 2)}</td>
                    <td className={clsx('num px-2 py-2 text-right font-semibold', pnlClass(v.expectancy_r))}>{fmtR(v.expectancy_r, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {cal?.source_job && (
              <button type="button" onClick={() => navigate(`/backtest/${cal.source_job}`)} className="mt-2 text-[11.5px] text-accent hover:underline">
                Kaynak testi aç →
              </button>
            )}
          </>
        ) : (
          <p className="text-[12px] leading-relaxed text-muted">
            Fırsatların kazanma olasılığı ve beklentisi şu an varsayılan değerlerle tahmin ediliyor. Bir testin sonuçlarını uygulayarak bu tahminleri
            ölçülmüş istatistiklere dayandırabilirsin.
          </p>
        )}
      </div>
    </Card>
  );
}

function JobsCard({ jobs, selectedId, loading, onDelete }: { jobs: BacktestJob[]; selectedId?: string; loading: boolean; onDelete: (j: BacktestJob) => void }) {
  return (
    <Card>
      <CardHeader title="Geçmiş testler" subtitle={jobs.length ? `${jobs.length} test` : undefined} icon={<HistoryIcon className="h-4 w-4" />} />
      {jobs.length ? (
        <div className="max-h-[420px] space-y-1 overflow-y-auto px-2 pb-2">
          {jobs.map((j) => {
            const meta = STATUS_META[j.status];
            const s = j.summary;
            return (
              <div
                key={j.id}
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/backtest/${j.id}`)}
                onKeyDown={(e) => e.key === 'Enter' && navigate(`/backtest/${j.id}`)}
                className={clsx(
                  'group flex cursor-pointer items-start gap-3 rounded-lg px-2.5 py-2.5 transition-colors',
                  j.id === selectedId ? 'bg-elevated ring-1 ring-inset ring-line' : 'hover:bg-hover/60',
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={meta.tone} dot>
                      {meta.label}
                      {isActive(j) && ` · %${Math.round(j.progress.pct)}`}
                    </Badge>
                    <span className="num text-[11.5px] text-subtle">{fmtDateTime(j.created_at)}</span>
                  </div>
                  <div className="mt-1 text-[12px] text-muted">
                    {j.config.days} gün · {universeText(j.config)} · {strategiesText(j.config.strategies)}
                  </div>
                  {s && j.status === 'done' && (
                    <div className="num mt-1 flex flex-wrap gap-x-3 text-[11.5px]">
                      <span className={pnlClass(s.total_return_pct)}>{pctU(s.total_return_pct)}</span>
                      <span className="text-muted">PF {isNum(s.profit_factor) ? fmtNum(s.profit_factor, 2) : '—'}</span>
                      <span className="text-muted">{s.trades} işlem</span>
                      <span className="text-down">DD {pctU(-Math.abs(s.max_drawdown_pct), false, 1)}</span>
                    </div>
                  )}
                </div>
                {!isActive(j) && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(j);
                    }}
                    className="rounded-md p-1.5 text-subtle opacity-0 transition-opacity hover:bg-hover hover:text-down group-hover:opacity-100 focus:opacity-100"
                    aria-label="Testi sil"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ) : loading ? (
        <div className="space-y-2 px-4 pb-4">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : (
        <Empty icon={<FlaskConical className="h-5 w-5" />} title="Henüz test yok" className="py-8">
          İlk testini başlat; sonuçlar burada listelenir.
        </Empty>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* İlerleme                                                             */
/* ------------------------------------------------------------------ */

function ProgressCard({ job, onCancel, cancelling }: { job: BacktestJob; onCancel: () => void; cancelling: boolean }) {
  const now = useNow(1000);
  const cur = job.progress.phase;
  const curIdx = cur === 'done' ? PHASES.length : PHASES.findIndex((p) => p.key === cur);
  const pct = Math.max(0, Math.min(100, job.progress.pct));
  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={job.status === 'queued' ? 'Test sırada' : 'Test çalışıyor'}
        subtitle={`${job.config.days} gün · ${universeText(job.config)} · ${strategiesText(job.config.strategies)} · ${fmtDuration(now - job.created_at)} geçti`}
        icon={<Loader2 className="h-4 w-4 animate-spin text-accent" />}
        right={
          <Button size="sm" variant="outline-danger" icon={<Square className="h-3.5 w-3.5" />} onClick={onCancel} loading={cancelling}>
            İptal et
          </Button>
        }
      />
      <div className="space-y-4 px-4 pb-5 pt-1">
        <div className="grid gap-2 sm:grid-cols-3">
          {PHASES.map((p, i) => {
            const done = i < curIdx;
            const active = i === curIdx;
            return (
              <div
                key={p.key}
                className={clsx(
                  'flex items-start gap-2.5 rounded-xl border px-3 py-2.5',
                  active ? 'border-accent/40 bg-accent/[0.06]' : done ? 'border-up/25 bg-up/[0.04]' : 'border-line',
                )}
              >
                <span className="mt-0.5">
                  {done ? (
                    <CheckCircle2 className="h-4 w-4 text-up" />
                  ) : active ? (
                    <Loader2 className="h-4 w-4 animate-spin text-accent" />
                  ) : (
                    <Circle className="h-4 w-4 text-subtle" />
                  )}
                </span>
                <span>
                  <span className={clsx('block text-[12.5px] font-medium', active ? 'text-fg' : done ? 'text-up' : 'text-muted')}>{p.label}</span>
                  <span className="block text-[11px] text-subtle">{p.desc}</span>
                </span>
              </div>
            );
          })}
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between text-[12px]">
            <span className="text-muted">{job.progress.message || 'Hazırlanıyor…'}</span>
            <span className="num font-semibold text-fg">%{Math.round(pct)}</span>
          </div>
          <div className="relative h-2 w-full overflow-hidden rounded-full bg-elevated">
            <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
            <div className="skeleton absolute inset-0 !bg-transparent" />
          </div>
        </div>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Sonuçlar                                                             */
/* ------------------------------------------------------------------ */

function Kpi({ label, value, cls, sub, hint }: { label: string; value: string; cls?: string; sub?: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0 px-4 py-3.5">
      <div className="label flex items-center gap-1">
        {label}
        {hint && <InfoTip>{hint}</InfoTip>}
      </div>
      <div className={clsx('num mt-1 truncate text-[20px] font-semibold tracking-tight', cls ?? 'text-fg')}>{value}</div>
      {sub && <div className="num mt-0.5 truncate text-[11px] text-subtle">{sub}</div>}
    </div>
  );
}

function RangeBar({
  marks,
  min,
  max,
  tone,
  reference,
}: {
  marks: { v: number; label: string; strong?: boolean }[];
  min: number;
  max: number;
  tone: 'down' | 'up' | 'accent';
  /** İnce dikey referans çizgisi (ör. başlangıç sermayesi). */
  reference?: { v: number; label: string };
}) {
  const span = max - min || 1;
  const pos = (v: number) => Math.max(0, Math.min(100, ((v - min) / span) * 100));
  const lo = Math.min(...marks.map((m) => m.v));
  const hi = Math.max(...marks.map((m) => m.v));
  const color = tone === 'down' ? 'bg-down' : tone === 'up' ? 'bg-up' : 'bg-accent';
  // Birbirine çok yakın işaretlerin etiketleri çakışmasın: ikinci satıra indir.
  let lastTop = -Infinity;
  const placed = [...marks]
    .sort((a, b) => a.v - b.v)
    .map((m) => {
      const p = pos(m.v);
      const row = p - lastTop < 16 ? 1 : 0;
      if (row === 0) lastTop = p;
      return { ...m, p, row };
    });
  const twoRows = placed.some((m) => m.row === 1);
  const align = (p: number) => (p < 8 ? 'left-0' : p > 92 ? 'right-0' : 'left-1/2 -translate-x-1/2');
  return (
    <div className={clsx('relative mt-2 h-2 rounded-full bg-elevated', twoRows ? 'mb-10' : 'mb-6')}>
      <div className={clsx('absolute inset-y-0 rounded-full opacity-35', color)} style={{ left: `${pos(lo)}%`, width: `${pos(hi) - pos(lo)}%` }} />
      {reference && (
        <div
          className="absolute -bottom-1.5 -top-1.5 w-px -translate-x-1/2 bg-fg/50"
          style={{ left: `${pos(reference.v)}%` }}
          title={reference.label}
          aria-label={reference.label}
        />
      )}
      {placed.map((m) => (
        <div key={m.label} className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ left: `${m.p}%` }}>
          <div className={clsx('h-3.5 w-[3px] rounded-sm', color, !m.strong && 'opacity-70')} />
          <div className={clsx('num absolute whitespace-nowrap text-[10px] text-subtle', m.row ? 'top-8' : 'top-4', align(m.p))}>{m.label}</div>
        </div>
      ))}
    </div>
  );
}

function MonteCarloCard({ mc, initial }: { mc: BacktestResult['monte_carlo']; initial: number }) {
  if (!mc) {
    return (
      <Card>
        <CardHeader title="Monte Carlo" icon={<Dices className="h-4 w-4" />} />
        <div className="px-4 pb-5 text-[12px] text-muted">Monte Carlo için yeterli işlem yok.</div>
      </Card>
    );
  }
  const dd50 = Math.abs(mc.dd_p50);
  const dd95 = Math.abs(mc.dd_p95);
  const dd99 = Math.abs(mc.dd_p99);
  const ddMax = Math.max(dd99 * 1.15, 5);
  const fMin = Math.min(mc.final_p5, initial) * 0.98;
  const fMax = Math.max(mc.final_p95, initial) * 1.02;
  return (
    <Card>
      <CardHeader
        title="Monte Carlo"
        subtitle={`${mc.runs.toLocaleString('tr-TR')} rastgele işlem sırası`}
        icon={<Dices className="h-4 w-4" />}
        right={
          <InfoTip>
            Aynı işlemler farklı sıralarla yaşansaydı ne olurdu? Kayıp serilerinin şansa bağlı etkisini gösterir. Gerçek riskin alt sınırı olarak düşünün.
          </InfoTip>
        }
      />
      <div className="space-y-4 px-4 pb-4">
        <p className="rounded-lg border border-line bg-bg/50 px-3 py-2.5 text-[12px] leading-relaxed text-muted">
          İşlem sırası {mc.runs.toLocaleString('tr-TR')} kez karıştırıldı; <b className="text-fg">%95 ihtimalle</b> en kötü düşüş{' '}
          <b className="num text-down">{pctU(dd95, false, 1)}</b>'i geçmez. Medyan senaryoda düşüş <span className="num text-fg">{pctU(dd50, false, 1)}</span>.
        </p>
        <div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="text-muted">Maksimum düşüş dağılımı</span>
            <span className="num text-down">p99 {pctU(dd99, false, 1)}</span>
          </div>
          <RangeBar
            min={0}
            max={ddMax}
            tone="down"
            marks={[
              { v: dd50, label: `p50 ${pctU(dd50, false, 1)}` },
              { v: dd95, label: `p95 ${pctU(dd95, false, 1)}`, strong: true },
              { v: dd99, label: `p99` },
            ]}
          />
        </div>
        <div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="text-muted">Dönem sonu özsermaye</span>
            <span className="num">
              <span className="text-subtle">başlangıç {fmtUsd(initial, { digits: 0 })} · </span>
              <span className="text-fg">medyan {fmtUsd(mc.final_p50)}</span>
            </span>
          </div>
          <RangeBar
            min={fMin}
            max={fMax}
            tone={mc.final_p50 >= initial ? 'up' : 'down'}
            reference={{ v: initial, label: `Başlangıç ${fmtUsd(initial)} USDT` }}
            marks={[
              { v: mc.final_p5, label: `p5 ${fmtUsd(mc.final_p5, { digits: 0 })}` },
              { v: mc.final_p50, label: `p50`, strong: true },
              { v: mc.final_p95, label: `p95 ${fmtUsd(mc.final_p95, { digits: 0 })}` },
            ]}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: '%20+ düşüş olasılığı', v: mc.prob_dd_20 },
            { label: '%30+ düşüş olasılığı', v: mc.prob_dd_30 },
          ].map((p) => (
            <div key={p.label} className="rounded-lg border border-line px-3 py-2.5">
              <div className="text-[11.5px] text-muted">{p.label}</div>
              <div className={clsx('num mt-0.5 text-[17px] font-semibold', p.v >= 0.25 ? 'text-down' : p.v >= 0.1 ? 'text-warn' : 'text-up')}>
                {fmtPct(p.v, { digits: 0 })}
              </div>
              <div className="mt-1.5 h-1 w-full rounded-full bg-elevated">
                <div
                  className={clsx('h-full rounded-full', p.v >= 0.25 ? 'bg-down' : p.v >= 0.1 ? 'bg-warn' : 'bg-up')}
                  style={{ width: `${Math.min(100, p.v * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function WalkForwardCard({ rows }: { rows: BacktestResult['walk_forward'] }) {
  const positive = rows.filter((r) => r.return_pct > 0).length;
  const tone = !rows.length ? 'neutral' : positive / rows.length >= 0.66 ? 'up' : positive / rows.length >= 0.5 ? 'warn' : 'down';
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Walk-forward tutarlılığı
            <InfoTip>Dönem eşit parçalara bölünür ve her parça ayrı değerlendirilir. Sonuçlar dönemlerin çoğunda pozitifse strateji tek bir şanslı döneme bağlı değildir.</InfoTip>
          </span>
        }
        subtitle="Dönemlere bölünmüş sonuçlar"
        icon={<CalendarRange className="h-4 w-4" />}
        right={rows.length ? <Badge tone={tone}>{`${positive}/${rows.length} dönem pozitif`}</Badge> : undefined}
      />
      {rows.length ? (
        <div className="overflow-x-auto pb-2">
          <table className="w-full min-w-[460px] text-[12.5px]">
            <thead className="table-head border-y border-line">
              <tr>
                <th>Dönem</th>
                <th>Tarih</th>
                <th className="!text-right">İşlem</th>
                <th className="!text-right">Beklenti</th>
                <th className="!text-right">PF</th>
                <th className="!text-right">Getiri</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.fold} className="table-row border-b border-line/60">
                  <td>
                    <span className="flex items-center gap-1.5 font-medium text-fg">
                      {r.return_pct > 0 ? <CheckCircle2 className="h-3.5 w-3.5 text-up" /> : <XCircle className="h-3.5 w-3.5 text-down" />}
                      {r.fold}
                    </span>
                  </td>
                  <td className="num whitespace-nowrap text-[11.5px] text-muted">{fmtDateRange(r.start, r.end)}</td>
                  <td className="num text-right text-muted">{r.trades}</td>
                  <td className={clsx('num text-right', pnlClass(r.expectancy_r))}>{fmtR(r.expectancy_r)}</td>
                  <td className="num text-right text-muted">{isNum(r.profit_factor) ? fmtNum(r.profit_factor, 2) : '—'}</td>
                  <td className={clsx('num text-right font-semibold', pnlClass(r.return_pct))}>{pctU(r.return_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-4 pb-5 text-[12px] text-muted">Walk-forward için yeterli veri yok.</div>
      )}
    </Card>
  );
}

type SortKey = 'entry_time' | 'r' | 'pnl';

function TradesTable({ trades }: { trades: BacktestTrade[] }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'entry_time', dir: -1 });
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState<'all' | 'win' | 'loss'>('all');
  const per = 25;
  const rows = useMemo(() => {
    const f = trades.filter((t) => (filter === 'win' ? t.pnl > 0 : filter === 'loss' ? t.pnl <= 0 : true));
    return [...f].sort((a, b) => (a[sort.key] - b[sort.key]) * sort.dir);
  }, [trades, sort, filter]);
  const pages = Math.max(1, Math.ceil(rows.length / per));
  const cur = Math.min(page, pages - 1);
  const slice = rows.slice(cur * per, cur * per + per);
  useEffect(() => setPage(0), [filter, sort]);

  const head = (key: SortKey, label: string, right = true) => (
    <th className={right ? '!text-right' : undefined}>
      <button
        type="button"
        onClick={() => setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: -1 }))}
        className={clsx('inline-flex items-center gap-1 uppercase', sort.key === key ? 'text-fg' : 'hover:text-muted')}
      >
        {label}
        {sort.key === key && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </th>
  );

  return (
    <Card>
      <CardHeader
        title="İşlemler"
        subtitle={`${rows.length} işlem${trades.length >= 2000 ? ' (ilk 2000 gösteriliyor)' : ''}`}
        icon={<ListOrdered className="h-4 w-4" />}
        right={
          <Segmented
            size="xs"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'Tümü' },
              { value: 'win', label: 'Kazanç' },
              { value: 'loss', label: 'Kayıp' },
            ]}
          />
        }
      />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-[12.5px]">
          <thead className="table-head border-y border-line">
            <tr>
              <th>Sembol</th>
              <th>Strateji</th>
              {head('entry_time', 'Giriş', false)}
              <th>Çıkış</th>
              <th className="!text-right">Süre</th>
              {head('r', 'R')}
              {head('pnl', 'K/Z')}
              <th>Neden</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((t, i) => (
              <tr key={`${t.symbol}-${t.entry_time}-${i}`} className="table-row border-b border-line/60">
                <td>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-fg">{t.symbol}</span>
                    <SideBadge side={t.side} size="sm" />
                  </div>
                </td>
                <td>
                  <Badge tone={strategyTone(t.strategy)}>{t.strategy}</Badge>
                </td>
                <td className="num whitespace-nowrap">
                  <div className="text-fg">{fmtPrice(t.entry_price)}</div>
                  <div className="text-[10.5px] text-subtle">{fmtDateTime(t.entry_time)}</div>
                </td>
                <td className="num whitespace-nowrap">
                  <div className="text-fg">{fmtPrice(t.exit_price)}</div>
                  <div className="text-[10.5px] text-subtle">{fmtDateTime(t.exit_time)}</div>
                </td>
                <td className="num whitespace-nowrap text-right text-muted">{fmtDuration(t.exit_time - t.entry_time)}</td>
                <td className={clsx('num text-right font-semibold', pnlClass(t.r))}>{fmtR(t.r)}</td>
                <td className={clsx('num text-right', pnlClass(t.pnl))}>{fmtUsd(t.pnl, { sign: true })}</td>
                <td className="max-w-[200px] truncate text-[12px] text-muted" title={t.reason}>
                  {t.reason}
                </td>
              </tr>
            ))}
            {!slice.length && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-[12px] text-subtle">
                  İşlem yok
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between gap-3 px-4 py-2.5 text-[12px] text-muted">
          <span className="num">
            {cur * per + 1}–{Math.min(rows.length, cur * per + per)} / {rows.length}
          </span>
          <div className="flex items-center gap-1">
            <Button size="xs" variant="ghost" icon={<ChevronLeft className="h-3.5 w-3.5" />} disabled={cur === 0} onClick={() => setPage(cur - 1)}>
              Önceki
            </Button>
            <span className="num px-2">
              {cur + 1}/{pages}
            </span>
            <Button size="xs" variant="ghost" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>
              Sonraki <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function ResultView({ job, result, onApply, applying, onDelete }: { job: BacktestJob; result: BacktestResult; onApply: () => void; applying: boolean; onDelete: () => void }) {
  const s = result.summary;
  const ret = s.total_return_pct;
  const dd = -Math.abs(s.max_drawdown_pct);
  const calibRows = Object.keys(result.calibration ?? {}).length;
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[16px] font-semibold tracking-tight text-fg">Sonuçlar</h2>
              <Badge tone="up" dot>
                Tamamlandı
              </Badge>
              {result.data_source === 'synthetic' ? (
                <Badge tone="warn">
                  <AlertTriangle className="h-3 w-3" /> Veri kaynağı: sentetik
                </Badge>
              ) : (
                <Badge tone="neutral">Veri kaynağı: Binance</Badge>
              )}
            </div>
            <div className="mt-1 text-[12px] text-muted">
              {fmtDateRange(s.period_start, s.period_end)} · {s.symbols} sembol · {strategiesText(job.config.strategies)} · başlangıç{' '}
              {fmtUsd(s.initial_equity)} USDT
              {job.finished_at ? ` · ${fmtDuration(job.finished_at - job.created_at)} sürdü` : ''}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="ghost" size="sm" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={onDelete}>
              Sil
            </Button>
            <Tooltip content={calibRows ? 'Fırsatların EV ve kazanma olasılığı tahminleri bu testin ölçülmüş istatistikleriyle güncellenir.' : 'Bu testte kalibrasyon için yeterli işlem yok.'}>
              <span>
                <Button variant="primary" size="sm" icon={<Sparkles className="h-3.5 w-3.5" />} onClick={onApply} loading={applying} disabled={!calibRows}>
                  Kalibrasyonu uygula
                </Button>
              </span>
            </Tooltip>
          </div>
        </div>
      </Card>

      {result.warnings.length > 0 && (
        <div className="space-y-1.5 rounded-xl border border-warn/30 bg-warn/[0.06] px-4 py-3">
          {result.warnings.map((w) => (
            <div key={w} className="flex items-start gap-2 text-[12.5px] text-warn">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {w}
            </div>
          ))}
        </div>
      )}

      <Card className="overflow-hidden">
        <div className="grid grid-cols-2 divide-x divide-y divide-line sm:grid-cols-4 xl:grid-cols-8 xl:divide-y-0">
          <Kpi label="Toplam getiri" value={pctU(ret)} cls={pnlClass(ret)} sub={`${fmtUsd(s.final_equity - s.initial_equity, { sign: true })} USDT`} />
          <Kpi label="Maks. düşüş" value={pctU(dd, true, 1)} cls="text-down" sub={isNum(s.calmar) ? `Calmar ${fmtNum(s.calmar, 2)}` : undefined} />
          <Kpi
            label="Profit factor"
            value={isNum(s.profit_factor) ? fmtNum(s.profit_factor, 2) : '—'}
            cls={isNum(s.profit_factor) ? (s.profit_factor >= 1.3 ? 'text-up' : s.profit_factor < 1 ? 'text-down' : 'text-warn') : undefined}
            hint="Brüt kâr / brüt zarar. 1,3 üstü sağlıklı kabul edilir."
          />
          <Kpi label="Beklenti" value={fmtR(s.expectancy_r)} cls={pnlClass(s.expectancy_r)} sub="işlem başına" />
          <Kpi
            label="Kazanma oranı"
            value={isNum(s.win_rate) ? fmtPct(s.win_rate, { digits: 1 }) : '—'}
            sub={<span title="Ortalama kazanç / ortalama kayıp (R)">{`${fmtR(s.avg_win_r)} / ${fmtR(s.avg_loss_r)}`}</span>}
          />
          <Kpi label="İşlem" value={String(s.trades)} sub={`${fmtNum(s.trades_per_day, 2)} / gün`} />
          <Kpi
            label="Sharpe"
            value={isNum(s.sharpe) ? fmtNum(s.sharpe, 2) : '—'}
            cls={isNum(s.sharpe) ? (s.sharpe >= 1 ? 'text-up' : s.sharpe < 0 ? 'text-down' : undefined) : undefined}
            sub={isNum(s.sortino) ? `Sortino ${fmtNum(s.sortino, 2)}` : undefined}
            hint="Günlük getirilerden yıllıklandırılmış risk-ayarlı getiri."
          />
          <Kpi label="CAGR" value={isNum(s.cagr_pct) ? pctU(s.cagr_pct) : '—'} cls={pnlClass(s.cagr_pct)} sub={isNum(s.cagr_pct) ? 'yıllık bileşik' : 'kısa dönem'} />
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-xl border border-line bg-surface/60 px-4 py-3 sm:grid-cols-3 xl:grid-cols-6">
        {[
          ['En iyi / en kötü', <span key="bw"><span className="text-up">{fmtR(s.best_r)}</span> / <span className="text-down">{fmtR(s.worst_r)}</span></span>],
          ['Ort. süre', isNum(s.avg_hold_hours) ? fmtDuration(s.avg_hold_hours * 3600_000) : '—'],
          ['Piyasada kalma', pctU(s.exposure_pct, false, 0)],
          ['Komisyon', `${fmtUsd(-Math.abs(s.fees_total), { sign: true })}`],
          ['Funding', <span key="f" className={pnlClass(s.funding_total)}>{fmtUsd(s.funding_total, { sign: true })}</span>],
          ['Son özsermaye', `${fmtUsd(s.final_equity)} USDT`],
        ].map(([k, v]) => (
          <div key={String(k)} className="min-w-0">
            <div className="text-[11px] text-subtle">{k}</div>
            <div className="num mt-0.5 truncate text-[12.5px] text-fg">{v}</div>
          </div>
        ))}
      </div>

      <Card className="overflow-hidden">
        <CardHeader title="Özsermaye ve düşüş" subtitle="Başlangıca göre renklendirilmiş; alt bölüm zirveden düşüş (%)" icon={<BarChart3 className="h-4 w-4" />} />
        <div className="px-2 pb-2">
          {result.equity.length > 1 ? (
            <EquityDrawdownChart data={result.equity} initial={s.initial_equity} height={360} />
          ) : (
            <div className="py-10 text-center text-[12px] text-subtle">Özsermaye verisi yok</div>
          )}
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="R dağılımı"
            subtitle="İşlem sonuçlarının risk birimi (R) cinsinden dağılımı"
            icon={<Gauge className="h-4 w-4" />}
            right={<InfoTip>1R, işlemde riske edilen tutardır. Sağlıklı bir trend stratejisinde çok sayıda küçük kayıp (−1R civarı) ve az sayıda büyük kazanç görülür.</InfoTip>}
          />
          <div className="px-4 pb-4 pt-2">
            <RHistogram bins={result.r_histogram} height={250} />
          </div>
        </Card>
        <MonteCarloCard mc={result.monte_carlo} initial={s.initial_equity} />
      </div>

      <Card>
        <CardHeader title="Aylık getiriler" subtitle="Yıl sütunu bileşik toplamdır" icon={<CalendarRange className="h-4 w-4" />} />
        <div className="px-3 pb-3">
          <MonthlyHeatmap values={result.monthly.map((m) => ({ month: m.month, value: m.return_pct }))} mode="pct" format={(v) => pctU(v, true, 1)} />
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <WalkForwardCard rows={result.walk_forward} />
        <Card>
          <CardHeader title="Yön bazında" icon={<Scale className="h-4 w-4" />} />
          <div className="overflow-x-auto pb-2">
            <table className="w-full min-w-[360px] text-[12.5px]">
              <thead className="table-head border-y border-line">
                <tr>
                  <th>Yön</th>
                  <th className="!text-right">İşlem</th>
                  <th className="!text-right">Kazanma</th>
                  <th className="!text-right">Beklenti</th>
                  <th className="!text-right">K/Z</th>
                </tr>
              </thead>
              <tbody>
                {result.by_side.map((b) => (
                  <tr key={b.side} className="table-row border-b border-line/60">
                    <td>
                      <SideBadge side={b.side} size="sm" />
                    </td>
                    <td className="num text-right text-muted">{b.trades}</td>
                    <td className="num text-right text-muted">{isNum(b.win_rate) ? fmtPct(b.win_rate, { digits: 0 }) : '—'}</td>
                    <td className={clsx('num text-right', pnlClass(b.expectancy_r))}>{fmtR(b.expectancy_r)}</td>
                    <td className={clsx('num text-right font-semibold', pnlClass(b.total_pnl))}>{fmtUsd(b.total_pnl, { sign: true })}</td>
                  </tr>
                ))}
                {!result.by_side.length && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-center text-[12px] text-subtle">
                      İşlem yok
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Strateji bazında" icon={<BarChart3 className="h-4 w-4" />} />
        <div className="overflow-x-auto pb-2">
          <table className="w-full min-w-[640px] text-[12.5px]">
            <thead className="table-head border-y border-line">
              <tr>
                <th>Strateji</th>
                <th className="!text-right">İşlem</th>
                <th className="!text-right">Kazanma</th>
                <th className="!text-right">PF</th>
                <th className="!text-right">Beklenti</th>
                <th className="!text-right">Ort. kazanç / kayıp</th>
                <th className="!text-right">K/Z</th>
              </tr>
            </thead>
            <tbody>
              {result.by_strategy.map((b) => (
                <tr key={b.strategy} className="table-row border-b border-line/60">
                  <td>
                    <span className="flex items-center gap-2 font-medium text-fg">
                      <Badge tone={strategyTone(b.strategy)}>{b.strategy}</Badge>
                      <span className="truncate">{b.strategy_name}</span>
                    </span>
                  </td>
                  <td className="num text-right text-muted">{b.trades}</td>
                  <td className="num text-right text-muted">{isNum(b.win_rate) ? fmtPct(b.win_rate, { digits: 0 }) : '—'}</td>
                  <td className={clsx('num text-right', isNum(b.profit_factor) && b.profit_factor < 1 ? 'text-down' : 'text-muted')}>
                    {isNum(b.profit_factor) ? fmtNum(b.profit_factor, 2) : '—'}
                  </td>
                  <td className={clsx('num text-right font-semibold', pnlClass(b.expectancy_r))}>{fmtR(b.expectancy_r)}</td>
                  <td className="num text-right">
                    <span className="text-up">{fmtR(b.avg_win_r)}</span> <span className="text-subtle">/</span> <span className="text-down">{fmtR(b.avg_loss_r)}</span>
                  </td>
                  <td className={clsx('num text-right font-semibold', pnlClass(b.total_pnl))}>{fmtUsd(b.total_pnl, { sign: true })}</td>
                </tr>
              ))}
              {!result.by_strategy.length && (
                <tr>
                  <td colSpan={7} className="px-3 py-4 text-center text-[12px] text-subtle">
                    İşlem yok
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <TradesTable trades={result.trades} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Seçili iş                                                            */
/* ------------------------------------------------------------------ */

function JobView({ id, onCalibrationChanged }: { id: string; onCalibrationChanged: (c: Calibration) => void }) {
  const light = useApp((s) => s.backtestJobs[id]);
  const [full, setFull] = useState<BacktestJob | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [applying, setApplying] = useState(false);
  const { confirm, node } = useConfirm();
  const status = light?.status ?? full?.status;
  const active = status === 'queued' || status === 'running';

  const fetchJob = async () => {
    try {
      const j = await api.backtestJob(id);
      setFull(j);
      setNotFound(false);
      useApp.getState().upsertBacktestJob(j);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
    }
  };

  useEffect(() => {
    setFull(null);
    setNotFound(false);
    void fetchJob();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // İş bitince sonucu çek (WS mesajı `result` taşımaz)
  useEffect(() => {
    if (status && !active && !full?.result && full?.status !== status) void fetchJob();
    if (status === 'done' && full && !full.result) void fetchJob();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // WS'e ek olarak 2 sn'de bir yoklama
  useInterval(() => void fetchJob(), active ? 2000 : null);

  const job = full && light ? { ...full, ...light, result: full.result } : (full ?? light);

  if (notFound) {
    return (
      <Card>
        <Empty icon={<XCircle className="h-5 w-5" />} title="Test bulunamadı" action={<Button onClick={() => navigate('/backtest')}>Geri dön</Button>}>
          Bu test silinmiş olabilir.
        </Empty>
      </Card>
    );
  }
  if (!job) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const cancel = async () => {
    setCancelling(true);
    try {
      const j = await withToast(api.cancelBacktest(job.id), 'Test iptal edilemedi');
      useApp.getState().upsertBacktestJob(j);
      setFull((f) => (f ? { ...f, ...j } : j));
    } catch {
      /* toast */
    } finally {
      setCancelling(false);
    }
  };

  const del = async () => {
    const ok = await confirm({ title: 'Test silinsin mi?', message: 'Bu testin sonuçları kalıcı olarak silinecek.', confirmText: 'Sil', tone: 'danger' });
    if (!ok) return;
    try {
      await withToast(api.deleteBacktest(job.id), 'Test silinemedi');
      useApp.getState().removeBacktestJob(job.id);
      navigate('/backtest');
    } catch {
      /* toast */
    }
  };

  const apply = async () => {
    const r = job.result;
    if (!r) return;
    const rows = Object.entries(r.calibration ?? {});
    const ok = await confirm({
      title: 'Kalibrasyon uygulansın mı?',
      message: 'Fırsatların kazanma olasılığı ve beklenti (EV) tahminleri, varsayılan değerler yerine bu testin ölçülmüş istatistikleriyle (canlı sonuçlarla harmanlanarak) hesaplanacak.',
      confirmText: 'Uygula',
      tone: 'primary',
      details: rows.length ? (
        <div className="space-y-1 rounded-lg border border-line bg-bg/60 px-3 py-2.5 text-[12px]">
          {rows.map(([k, v]) => (
            <div key={k} className="num flex items-center justify-between gap-4">
              <span className="text-fg">{k}</span>
              <span className="text-muted">
                n {v.n} · kazanma {fmtPct(v.win_rate, { digits: 0 })} · beklenti <span className={pnlClass(v.expectancy_r)}>{fmtR(v.expectancy_r)}</span>
              </span>
            </div>
          ))}
        </div>
      ) : undefined,
    });
    if (!ok) return;
    setApplying(true);
    try {
      const c = await withToast(api.applyCalibration(job.id), 'Kalibrasyon uygulanamadı');
      onCalibrationChanged(c);
      toast.success('Kalibrasyon uygulandı', 'EV tahminleri artık ölçülmüş istatistiklere dayanıyor.');
    } catch {
      /* toast */
    } finally {
      setApplying(false);
    }
  };

  return (
    <>
      {node}
      {active ? (
        <ProgressCard job={job} onCancel={cancel} cancelling={cancelling} />
      ) : job.status === 'failed' ? (
        <Card className="border-down/30">
          <CardHeader title="Test başarısız oldu" icon={<XCircle className="h-4 w-4 text-down" />} subtitle={fmtDateTime(job.created_at)} right={<Button size="sm" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={del}>Sil</Button>} />
          <div className="px-4 pb-4 text-[12.5px] text-down">{job.error || 'Bilinmeyen hata'}</div>
        </Card>
      ) : job.status === 'cancelled' ? (
        <Card>
          <CardHeader title="Test iptal edildi" icon={<Square className="h-4 w-4 text-warn" />} subtitle={fmtDateTime(job.created_at)} right={<Button size="sm" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={del}>Sil</Button>} />
          <div className="px-4 pb-4 text-[12.5px] text-muted">Test tamamlanmadan durduruldu.</div>
        </Card>
      ) : job.result ? (
        <ResultView job={job} result={job.result} onApply={apply} applying={applying} onDelete={del} />
      ) : (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Sayfa                                                                */
/* ------------------------------------------------------------------ */

export function Backtest({ id }: { id?: string }) {
  const jobsMap = useApp((s) => s.backtestJobs);
  const allJobs = useMemo(() => Object.values(jobsMap).sort((a, b) => b.created_at - a.created_at), [jobsMap]);
  const jobs = useMemo(() => allJobs.filter((j) => j.kind !== 'optimize'), [allJobs]);
  const list = useAsync<BacktestJob[]>(
    () =>
      api.backtestJobs().then((l) => {
        useApp.getState().setBacktestJobs(l);
        return l;
      }),
    [],
  );
  const cal = useAsync<Calibration>(() => api.calibration(), []);
  const { confirm, node } = useConfirm();
  const running = allJobs.find(isActive);
  const selectedId = id ?? jobs[0]?.id;

  const resetCal = async () => {
    const ok = await confirm({
      title: 'Varsayılan istatistiklere dönülsün mü?',
      message: 'EV ve kazanma olasılığı tahminleri yeniden strateji varsayılanlarıyla (ve canlı sonuçlarla) hesaplanır.',
      confirmText: 'Varsayılana dön',
      tone: 'primary',
    });
    if (!ok) return;
    try {
      const c = await withToast(api.resetCalibration(), 'Kalibrasyon sıfırlanamadı');
      cal.setData(c);
      toast.success('Varsayılan istatistiklere dönüldü');
    } catch {
      /* toast */
    }
  };

  const deleteJob = async (j: BacktestJob) => {
    const ok = await confirm({ title: 'Test silinsin mi?', message: `${fmtDateTime(j.created_at)} tarihli test kalıcı olarak silinecek.`, confirmText: 'Sil', tone: 'danger' });
    if (!ok) return;
    try {
      await withToast(api.deleteBacktest(j.id), 'Test silinemedi');
      useApp.getState().removeBacktestJob(j.id);
      if (j.id === id) navigate('/backtest');
    } catch {
      /* toast */
    }
  };

  return (
    <div className="space-y-4">
      {node}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">Backtest</h1>
          <p className="mt-0.5 max-w-3xl text-[12.5px] text-muted">
            Stratejileri geçmiş piyasa verisinde, canlıdaki aynı risk ve emir kurallarıyla test et. Sonuçları kalibrasyon olarak uygulayıp fırsat
            tahminlerini ölçülmüş verilere dayandırabilirsin.
          </p>
        </div>
      </div>

      <AutoOptimize blocked={!!running && running.kind !== 'optimize'} onApplied={() => void cal.reload()} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <ConfigCard blocked={running} onStarted={(j) => navigate(`/backtest/${j.id}`)} />
        <div className="space-y-4">
          <CalibrationCard cal={cal.data} loading={cal.loading} onReset={resetCal} />
          <JobsCard jobs={jobs} selectedId={selectedId} loading={list.loading} onDelete={deleteJob} />
        </div>
      </div>

      {selectedId ? <JobView key={selectedId} id={selectedId} onCalibrationChanged={(c) => cal.setData(c)} /> : null}
      {list.error && !jobs.length && (
        <Card className="p-4 text-[12.5px] text-muted">Testler alınamadı: {list.error}</Card>
      )}
    </div>
  );
}
