import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Sparkles, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { OrderPreview, Signal } from '../../api/types';
import { useDebounced } from '../../lib/hooks';
import { fmtDateTime, fmtLev, fmtNum, fmtPct, fmtPrice, fmtQty, fmtR, fmtUsd, tfLabel } from '../../lib/format';
import { navigate } from '../../lib/router';
import { useApp } from '../../store/app';
import { TradeChart } from '../charts/TradeChart';
import { NumberInput, Slider } from '../ui/form';
import { Drawer, InfoTip, useConfirm } from '../ui/overlay';
import { Badge, Button, SideBadge, Switch } from '../ui/primitives';
import { ScoreRing } from '../ui/viz';
import { MULTIPLIER_LABELS, signalOverlay, strategyTone } from './helpers';
import { Countdown, statusText } from './SignalCard';
import { useRiskBounds } from './useRiskBounds';

const FEATURE_LABELS: Record<string, string> = {
  vol_z: 'Hacim z-skoru',
  volume_z: 'Hacim z-skoru',
  atr: 'ATR',
  atr_pct: 'ATR (fiyata oran)',
  adx: 'ADX',
  adx_1h: 'ADX (1 sa)',
  rsi: 'RSI',
  funding: 'Funding oranı',
  funding_rate: 'Funding oranı',
  taker_ratio: 'Taker alış oranı',
  bb_width_pct: 'Bollinger genişliği yüzdeliği',
  squeeze_pct: 'Sıkışma yüzdeliği',
  ema_dist_atr: "EMA20'ye uzaklık (ATR)",
  rs_rank: 'Göreli güç sırası',
  momentum: 'Risk-ayarlı momentum',
  alpha_24h: 'Alfa 24 sa',
  beta: "BTC'ye beta",
  oi_change: 'OI değişimi',
  donchian_high: 'Donchian üst',
  donchian_low: 'Donchian alt',
};

const PCT_FEATURES = new Set(['atr_pct', 'funding', 'funding_rate', 'oi_change', 'alpha_24h']);

function featureValue(k: string, v: number): string {
  if (PCT_FEATURES.has(k)) return fmtPct(v, { sign: k !== 'atr_pct', digits: k.startsWith('funding') ? 4 : 2 });
  if (Math.abs(v) >= 1000) return fmtNum(v, 0);
  if (Math.abs(v) >= 10) return fmtNum(v, 1);
  return fmtNum(v, 2);
}

export function PlanPanel({ preview, loading, stop }: { preview: OrderPreview | null; loading: boolean; stop?: number | null }) {
  const p = preview?.plan;
  const entry = preview?.entry ?? 0;
  if (!p)
    return (
      <div className="rounded-xl border border-line bg-bg/60 px-4 py-6 text-center text-[12.5px] text-subtle">
        {loading ? 'Plan hesaplanıyor…' : 'Plan bekleniyor'}
      </div>
    );
  if (!p.ok)
    return (
      <div className="flex items-start gap-2.5 rounded-xl border border-warn/30 bg-warn/[0.07] px-4 py-3.5 text-[12.5px] text-warn">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div>
          <div className="font-semibold">Risk motoru bu işlemi reddediyor</div>
          <div className="mt-0.5 text-warn/90">{p.reason}</div>
        </div>
      </div>
    );
  const mults = Object.entries(p.multipliers ?? {});
  return (
    <div className={clsx('rounded-xl border border-line bg-bg/60 transition-opacity', loading && 'opacity-60')}>
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-t-xl bg-line">
        <div className="bg-surface px-4 py-3">
          <div className="label">Önerilen kaldıraç</div>
          <div className="num mt-1 text-[22px] font-semibold text-accent">{fmtLev(p.leverage)}</div>
        </div>
        <div className="bg-surface px-4 py-3">
          <div className="label">Risk</div>
          <div className="num mt-1 text-[22px] font-semibold text-down">{fmtUsd(p.risk_usdt)}</div>
          <div className="num text-[11px] text-subtle">özsermayenin {fmtPct(p.risk_pct)}</div>
        </div>
      </div>
      <dl className="divide-y divide-line px-4">
        <div className="kv">
          <dt>Pozisyon büyüklüğü</dt>
          <dd>{fmtUsd(p.notional)} USDT</dd>
        </div>
        <div className="kv">
          <dt>Miktar</dt>
          <dd>{fmtQty(p.qty)}</dd>
        </div>
        <div className="kv">
          <dt>Kullanılacak marjin</dt>
          <dd>{fmtUsd(p.margin)} USDT</dd>
        </div>
        <div className="kv">
          <dt>Stop mesafesi</dt>
          <dd>{stop && entry ? fmtPct(Math.abs(entry - stop) / entry) : '—'}</dd>
        </div>
        <div className="kv">
          <dt className="flex items-center gap-1">
            Tahmini likidasyon
            <InfoTip>Likidasyon fiyatının stop'tan en az k kat uzakta kalması kaldıraç tavanını belirler.</InfoTip>
          </dt>
          <dd>
            <span className="text-warn">{fmtPrice(p.liq_price)}</span>{' '}
            {entry > 0 && p.liq_price > 0 && <span className="text-subtle">({fmtPct(Math.abs(entry - p.liq_price) / entry)})</span>}
          </dd>
        </div>
        <div className="kv">
          <dt>Tahmini komisyon</dt>
          <dd>{fmtUsd(p.fee_estimate)} USDT</dd>
        </div>
      </dl>
      {mults.length > 0 && (
        <div className="border-t border-line px-4 py-3">
          <div className="label mb-2">Risk çarpanları</div>
          <div className="flex flex-wrap gap-1.5">
            {mults.map(([k, v]) => (
              <span key={k} className={clsx('chip num', v < 1 ? 'text-warn' : v > 1 ? 'text-up' : '')}>
                {MULTIPLIER_LABELS[k] ?? k} ×{fmtNum(v, 2)}
              </span>
            ))}
          </div>
        </div>
      )}
      {p.notes.length > 0 && (
        <ul className="space-y-1 border-t border-line px-4 py-3 text-[11.5px] text-muted">
          {p.notes.map((n) => (
            <li key={n} className="flex gap-1.5">
              <span className="text-subtle">•</span>
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SignalDrawer({
  signal,
  onClose,
  confirmOnOpen,
}: {
  signal: Signal | null;
  onClose: () => void;
  confirmOnOpen?: boolean;
}) {
  const status = useApp((s) => s.status);
  const bounds = useRiskBounds();
  const [riskPct, setRiskPct] = useState<number | null>(null);
  const [manualLev, setManualLev] = useState(false);
  const [lev, setLev] = useState<number | null>(null);
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const { confirm, node } = useConfirm();

  useEffect(() => {
    const planRisk = signal?.plan?.ok && signal.plan.risk_pct > 0 ? signal.plan.risk_pct : null;
    setRiskPct(planRisk ?? status?.limits.base_risk_pct ?? null);
    setManualLev(false);
    setLev(null);
    setPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal?.id]);

  const dRisk = useDebounced(riskPct, 250);
  const dLev = useDebounced(manualLev ? lev : null, 250);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!signal || signal.status !== 'ACTIVE') return;
    const t = window.setInterval(() => setTick((x) => x + 1), 5000);
    return () => window.clearInterval(t);
  }, [signal?.id, signal?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!signal || signal.status !== 'ACTIVE') return;
    let alive = true;
    setPreviewLoading(true);
    api
      .signalPreview(signal.id, {
        risk_pct: dRisk && dRisk > 0 ? dRisk : undefined,
        leverage: dLev && dLev >= 1 ? dLev : undefined,
      })
      .then((p) => alive && setPreview(p))
      .catch(() => alive && setPreview(null))
      .finally(() => alive && setPreviewLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal?.id, signal?.status, dRisk, dLev, tick]);

  const overlay = useMemo(() => (signal ? signalOverlay(signal) : null), [signal?.id, signal?.entry, signal?.stop, signal?.plan?.liq_price]); // eslint-disable-line react-hooks/exhaustive-deps
  const autoConfirmedRef = useRef<string | null>(null);
  const executeRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!confirmOnOpen || !signal || signal.status !== 'ACTIVE') return;
    if (autoConfirmedRef.current === signal.id) return;
    autoConfirmedRef.current = signal.id;
    const t = window.setTimeout(() => executeRef.current?.(), 350);
    return () => window.clearTimeout(t);
  }, [confirmOnOpen, signal]);
  useEffect(() => {
    if (!signal) autoConfirmedRef.current = null;
  }, [signal]);

  if (!signal) return null;
  const s = signal;
  const active = s.status === 'ACTIVE';
  const plan = preview?.plan ?? s.plan;

  const execute = async () => {
    const p = plan;
    const ok = await confirm({
      title: `${s.symbol} ${s.side === 'LONG' ? 'long' : 'short'} pozisyon açılsın mı?`,
      message: `${s.strategy_name} sinyali ${status?.env === 'live' ? 'CANLI hesapta' : status?.env === 'testnet' ? 'testnet hesabında' : 'simülasyonda'} işleme alınacak.`,
      confirmText: s.side === 'LONG' ? 'Long aç' : 'Short aç',
      tone: status?.env === 'live' ? 'danger' : 'success',
      details: p?.ok ? (
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-lg border border-line bg-bg/60 px-4 py-3 text-[12.5px]">
          <div className="flex justify-between gap-2">
            <span className="text-muted">Kaldıraç</span>
            <span className="num text-fg">{fmtLev(p.leverage)}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-muted">Büyüklük</span>
            <span className="num text-fg">{fmtUsd(p.notional)}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-muted">Marjin</span>
            <span className="num text-fg">{fmtUsd(p.margin)}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-muted">Risk</span>
            <span className="num text-down">{fmtUsd(p.risk_usdt)}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-muted">Stop</span>
            <span className="num text-fg">{fmtPrice(s.stop)}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span className="text-muted">Hedefler</span>
            <span className="num text-fg">{s.take_profits.length} kademe</span>
          </div>
        </div>
      ) : undefined,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const pos = await withToast(
        api.executeSignal(s.id, { risk_pct: riskPct && riskPct > 0 ? riskPct : undefined, leverage: manualLev && lev && lev >= 1 ? lev : undefined }),
        'İşleme girilemedi',
      );
      useApp.getState().upsertPosition(pos); // "Pozisyon açıldı" bildirimi sunucudan gelir
      onClose();
      navigate(`/pozisyon/${pos.id}`);
    } catch {
      /* toast gösterildi */
    } finally {
      setBusy(false);
    }
  };

  executeRef.current = () => void execute();

  const dismiss = async () => {
    try {
      const upd = await withToast(api.dismissSignal(s.id), 'Fırsat yoksayılamadı');
      useApp.getState().upsertSignal(upd);
      onClose();
    } catch {
      /* toast */
    }
  };

  const features = Object.entries(s.features ?? {}).filter(([, v]) => typeof v === 'number' && Number.isFinite(v));

  return (
    <Drawer
      open={!!signal}
      onClose={onClose}
      title={
        <div className="flex flex-wrap items-center gap-2.5">
          <ScoreRing score={s.score} size={36} />
          <span className="text-[17px] font-semibold tracking-tight">{s.symbol}</span>
          <SideBadge side={s.side} />
          <Badge tone={strategyTone(s.strategy)}>{s.strategy_name}</Badge>
          <span className="text-[12px] font-normal text-subtle">{tfLabel(s.timeframe)}</span>
          {!active && <Badge>{statusText(s.status)}</Badge>}
        </div>
      }
      subtitle={
        <span className="flex flex-wrap items-center gap-x-3">
          <span>Oluşturuldu {fmtDateTime(s.created_at)}</span>
          {active && (
            <span className="flex items-center gap-1">
              Geçerlilik <Countdown until={s.expires_at} />
            </span>
          )}
        </span>
      }
      footer={
        active ? (
          <div className="flex flex-wrap items-center gap-2">
            {status?.auto_mode ? (
              <span className="flex items-center gap-1.5 text-[12px] text-up">
                <Sparkles className="h-3.5 w-3.5" /> Otomatik mod açık: uygun fırsatlar sistem tarafından işleniyor. Yine de elle girebilirsin.
              </span>
            ) : (
              <span className="text-[12px] text-muted">Otomatik mod kapalı — bu işlemi senin onayınla açarız.</span>
            )}
            <div className="flex-1" />
            <Button variant="ghost" icon={<X className="h-4 w-4" />} onClick={dismiss}>
              Yoksay
            </Button>
            <Button
              variant={s.side === 'LONG' ? 'success' : 'danger'}
              size="lg"
              loading={busy}
              disabled={!plan?.ok}
              onClick={execute}
              icon={<CheckCircle2 className="h-4 w-4" />}
            >
              {s.side === 'LONG' ? 'Long pozisyon aç' : 'Short pozisyon aç'}
            </Button>
          </div>
        ) : undefined
      }
    >
      {node}
      <div className="grid gap-4 p-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-4">
          <div className="card overflow-hidden">
            <TradeChart symbol={s.symbol} interval={s.timeframe} overlay={overlay} height={400} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="card">
              <div className="card-head">
                <div className="card-title">Seviyeler</div>
              </div>
              <dl className="divide-y divide-line px-4 pb-2">
                <div className="kv">
                  <dt>Giriş (sinyal)</dt>
                  <dd>{fmtPrice(s.entry)}</dd>
                </div>
                <div className="kv">
                  <dt>Anlık fiyat</dt>
                  <dd>{fmtPrice(s.price)}</dd>
                </div>
                <div className="kv">
                  <dt>Stop-loss</dt>
                  <dd className="text-down">
                    {fmtPrice(s.stop)} <span className="text-subtle">({fmtPct((s.stop - s.entry) / s.entry, { sign: true })})</span>
                  </dd>
                </div>
                {s.take_profits.map((t, i) => (
                  <div className="kv" key={i}>
                    <dt>
                      TP{i + 1} · {fmtR(t.r, 1)} · %{Math.round(t.fraction * 100)}
                    </dt>
                    <dd className="text-up">
                      {fmtPrice(t.price)} <span className="text-subtle">({fmtPct((t.price - s.entry) / s.entry, { sign: true })})</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
            <div className="card">
              <div className="card-head">
                <div className="card-title flex items-center gap-1.5">
                  İstatistiksel değerlendirme
                  <InfoTip>
                    Beklenti ve kazanma olasılığı stratejinin varsayılan/backtest istatistiklerine dayanan tahminlerdir; garanti değildir.
                  </InfoTip>
                </div>
              </div>
              <dl className="divide-y divide-line px-4 pb-2">
                <div className="kv">
                  <dt>Puan</dt>
                  <dd>{Math.round(s.score)} / 100</dd>
                </div>
                <div className="kv">
                  <dt>Tahmini beklenti</dt>
                  <dd className={s.ev_r >= 0 ? 'text-up' : 'text-down'}>{fmtR(s.ev_r)}</dd>
                </div>
                <div className="kv">
                  <dt>Kazanma olasılığı</dt>
                  <dd>{fmtPct(s.win_prob, { digits: 0 })}</dd>
                </div>
                <div className="kv">
                  <dt>Risk : Ödül</dt>
                  <dd>1 : {fmtNum(s.rr, 1)}</dd>
                </div>
              </dl>
            </div>
          </div>
          <div className="card">
            <div className="card-head">
              <div className="card-title">Sinyal gerekçeleri</div>
            </div>
            <div className="space-y-3 px-4 pb-4">
              <div className="flex flex-wrap gap-1.5">
                {s.reasons.map((r) => (
                  <span key={r} className="chip text-fg">
                    <CheckCircle2 className="h-3 w-3 text-up" />
                    {r}
                  </span>
                ))}
                {!s.reasons.length && <span className="text-[12px] text-subtle">Gerekçe yok</span>}
              </div>
              {s.warnings.length > 0 && (
                <div className="space-y-1.5 rounded-lg border border-warn/25 bg-warn/[0.06] px-3 py-2.5">
                  {s.warnings.map((w) => (
                    <div key={w} className="flex items-start gap-1.5 text-[12px] text-warn">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {w}
                    </div>
                  ))}
                </div>
              )}
              {features.length > 0 && (
                <div className="grid grid-cols-2 gap-x-6 sm:grid-cols-3">
                  {features.map(([k, v]) => (
                    <div key={k} className="kv border-b border-line/60">
                      <dt className="truncate">{FEATURE_LABELS[k] ?? k}</dt>
                      <dd>{featureValue(k, v)}</dd>
                    </div>
                  ))}
                </div>
              )}
              {!active && s.status_note && <div className="text-[12px] text-subtle">{s.status_note}</div>}
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card">
            <div className="card-head">
              <div className="card-title">İşlem planı</div>
              {previewLoading && <span className="text-[11px] text-subtle">güncelleniyor…</span>}
            </div>
            <div className="space-y-4 px-4 pb-4">
              {active ? (
                <>
                  <div>
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="flex items-center gap-1 text-[12px] font-medium text-muted">
                        İşlem riski
                        <InfoTip>Stop tetiklenirse kaybedilecek tutarın özsermayeye oranı. Büyüklük ve kaldıraç buna göre hesaplanır.</InfoTip>
                      </span>
                      <span className="num text-[13px] font-semibold text-fg">{fmtPct(riskPct ?? 0)}</span>
                    </div>
                    <Slider
                      value={riskPct ?? bounds.base}
                      min={bounds.min}
                      max={bounds.max}
                      step={0.0005}
                      onChange={setRiskPct}
                      tone={(riskPct ?? 0) > 0.015 ? 'down' : (riskPct ?? 0) > 0.01 ? 'warn' : 'accent'}
                    />
                    <div className="num mt-1 flex justify-between text-[10.5px] text-subtle">
                      <span>{fmtPct(bounds.min)}</span>
                      <button type="button" className="hover:text-fg" onClick={() => setRiskPct(bounds.base)}>
                        sistem: {fmtPct(bounds.base)}
                      </button>
                      <span>{fmtPct(bounds.max)}</span>
                    </div>
                  </div>
                  <div className="rounded-lg border border-line px-3 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-[12.5px] font-medium text-fg">Kaldıracı elle belirle</div>
                        <div className="text-[11.5px] text-subtle">Kapalıyken sistem en güvenli kaldıracı seçer</div>
                      </div>
                      <Switch checked={manualLev} onChange={setManualLev} size="sm" />
                    </div>
                    {manualLev && (
                      <div className="mt-2.5">
                        <NumberInput
                          value={lev}
                          onChange={(v) => setLev(v === null ? null : Math.round(v))}
                          min={1}
                          max={125}
                          step={1}
                          suffix="x"
                          decimals={0}
                          allowEmpty
                          placeholder={plan?.ok ? `${plan.leverage}` : 'ör. 5'}
                        />
                        <div className="mt-1 text-[11px] text-subtle">Güvenlik tavanları (likidasyon tamponu, sınıf tavanı) yine uygulanır.</div>
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="text-[12px] text-muted">Bu fırsat artık aktif değil ({statusText(s.status)}).</div>
              )}
              <PlanPanel
                preview={preview ?? (s.plan ? { plan: s.plan, entry: s.entry, take_profits: s.take_profits } : null)}
                loading={previewLoading}
                stop={s.stop}
              />
            </div>
          </div>
        </div>
      </div>
    </Drawer>
  );
}
