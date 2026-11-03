import clsx from 'clsx';
import { AlertTriangle, ChevronRight, Clock, Eye, Layers, Sparkles, X } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Signal } from '../../api/types';
import { useNow } from '../../lib/hooks';
import { fmtCountdown, fmtLev, fmtPct, fmtPrice, fmtR, fmtUsd, pnlClass, tfLabel } from '../../lib/format';
import { Badge, Button, SideBadge } from '../ui/primitives';
import { ScoreRing } from '../ui/viz';
import { strategyTone } from './helpers';

export function Countdown({ until, className }: { until: number; className?: string }) {
  const now = useNow(1000);
  const left = until - now;
  return (
    <span
      className={clsx(
        'num inline-flex items-center gap-1 text-[11.5px]',
        left <= 0 ? 'text-subtle' : left < 120_000 ? 'text-warn' : 'text-muted',
        className,
      )}
    >
      <Clock className="h-3 w-3" />
      {fmtCountdown(left)}
    </span>
  );
}

/**
 * Sinyal özelliklerinden kısa göstergeler: zaman dilimi uyumu (mtf_aligned 1-3),
 * open interest değişimi (oi_change_1h, yüzde) ve kalibre edilmiş EV (calibrated = 1).
 */
export function SignalInsightChips({ features, className }: { features: Record<string, number> | undefined; className?: string }) {
  const f = features ?? {};
  const items: ReactNode[] = [];
  const mtf = f.mtf_aligned;
  if (typeof mtf === 'number' && Number.isFinite(mtf)) {
    items.push(
      <Badge
        key="mtf"
        tone={mtf >= 3 ? 'up' : mtf >= 2 ? 'accent' : 'neutral'}
        title="15 dk · 1 sa · 4 sa trendlerinden sinyal yönüyle uyumlu olanların sayısı"
      >
        <Layers className="h-3 w-3" />
        Zaman dilimi uyumu {Math.round(mtf)}/3
      </Badge>,
    );
  }
  const oi = f.oi_change_1h;
  if (typeof oi === 'number' && Number.isFinite(oi)) {
    items.push(
      <Badge key="oi" tone="neutral" title="Açık pozisyon (open interest) son 1 saatteki değişimi">
        OI 1 sa <span className={pnlClass(oi)}>{fmtPct(oi / 100, { sign: true, digits: 1 })}</span>
      </Badge>,
    );
  }
  if ((f.calibrated ?? 0) >= 0.5) {
    items.push(
      <Badge key="cal" tone="proj" title="Beklenti ve kazanma olasılığı uygulanan backtest kalibrasyonundan hesaplandı">
        <Sparkles className="h-3 w-3" />
        Kalibre EV
      </Badge>,
    );
  }
  if (!items.length) return null;
  return <div className={clsx('flex flex-wrap items-center gap-1.5', className)}>{items}</div>;
}

function Level({ label, price, entry, tone }: { label: string; price: number; entry: number; tone?: 'up' | 'down' | 'neutral' }) {
  const d = (price - entry) / entry;
  return (
    <div className="min-w-0">
      <div className="label">{label}</div>
      <div className={clsx('num mt-0.5 truncate text-[12.5px]', tone === 'down' ? 'text-down' : tone === 'up' ? 'text-up' : 'text-fg')}>
        {fmtPrice(price)}
      </div>
      {label !== 'Giriş' && <div className="num text-[10.5px] text-subtle">{fmtPct(d, { sign: true })}</div>}
    </div>
  );
}

export function SignalCard({
  signal: s,
  onOpen,
  onExecute,
  onDismiss,
  autoMode,
}: {
  signal: Signal;
  onOpen: () => void;
  onExecute: () => void;
  onDismiss: () => void;
  autoMode: boolean;
}) {
  const active = s.status === 'ACTIVE';
  const drift = s.price != null ? (s.price - s.entry) / s.entry : null;
  const plan = s.plan;
  // Zaman dilimi uyumu başlıkta çip olarak gösterildiğinde aynı gerekçeyi tekrar listeleme.
  const reasons =
    typeof s.features?.mtf_aligned === 'number' ? s.reasons.filter((r) => !r.startsWith('Zaman dilimi uyumu')) : s.reasons;
  return (
    <div
      className={clsx(
        'card group flex flex-col transition-all duration-200 hover:border-line-strong',
        !active && 'opacity-70',
      )}
    >
      <button type="button" onClick={onOpen} className="flex items-start gap-3 px-4 pb-3 pt-4 text-left">
        <ScoreRing score={s.score} size={44} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[15px] font-semibold tracking-tight text-fg">{s.symbol}</span>
            <SideBadge side={s.side} />
            <Badge tone={strategyTone(s.strategy)}>{s.strategy_name}</Badge>
            <span className="text-[11.5px] text-subtle">{tfLabel(s.timeframe)}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12px] text-muted">
            <span className="num">
              Fiyat <span className="text-fg">{fmtPrice(s.price ?? s.entry)}</span>
              {drift !== null && (
                <span className={clsx('ml-1', Math.abs(drift) > 0.004 ? 'text-warn' : 'text-subtle')}>
                  ({fmtPct(drift, { sign: true })} girişten)
                </span>
              )}
            </span>
          </div>
          <SignalInsightChips features={s.features} className="mt-2" />
        </div>
        <div className="flex flex-col items-end gap-1">
          {active ? <Countdown until={s.expires_at} /> : <Badge tone="neutral">{statusText(s.status)}</Badge>}
          <ChevronRight className="h-4 w-4 text-subtle transition-transform group-hover:translate-x-0.5" />
        </div>
      </button>

      <div className="grid grid-cols-4 gap-3 border-t border-line px-4 py-3">
        <Level label="Giriş" price={s.entry} entry={s.entry} />
        <Level label="Stop" price={s.stop} entry={s.entry} tone="down" />
        {s.take_profits.slice(0, 2).map((t, i) => (
          <Level key={i} label={`TP${i + 1}`} price={t.price} entry={s.entry} tone="up" />
        ))}
        {s.take_profits.length < 2 && <div />}
      </div>

      <div className="grid grid-cols-3 gap-3 border-t border-line px-4 py-3">
        <div>
          <div className="label">Beklenti</div>
          <div className={clsx('num mt-0.5 text-[13px] font-semibold', s.ev_r >= 0 ? 'text-up' : 'text-down')}>{fmtR(s.ev_r)}</div>
        </div>
        <div>
          <div className="label">Kazanma</div>
          <div className="num mt-0.5 text-[13px] text-fg">{fmtPct(s.win_prob, { digits: 0 })}</div>
        </div>
        <div>
          <div className="label">R:R</div>
          <div className="num mt-0.5 text-[13px] text-fg">1 : {s.rr.toLocaleString('tr-TR', { maximumFractionDigits: 1 })}</div>
        </div>
      </div>

      <div className="border-t border-line px-4 py-3">
        {plan ? (
          plan.ok ? (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
              <span className="label">Öneri</span>
              <span className="num text-fg">
                <span className="font-semibold text-accent">{fmtLev(plan.leverage)}</span> kaldıraç
              </span>
              <span className="num text-muted">
                Büyüklük <span className="text-fg">{fmtUsd(plan.notional)}</span>
              </span>
              <span className="num text-muted">
                Marjin <span className="text-fg">{fmtUsd(plan.margin)}</span>
              </span>
              <span className="num text-muted">
                Risk <span className="text-down">{fmtUsd(plan.risk_usdt)}</span> ({fmtPct(plan.risk_pct)})
              </span>
            </div>
          ) : (
            <div className="flex items-start gap-2 text-[12px] text-warn">
              <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
              <span>Risk motoru reddetti: {plan.reason}</span>
            </div>
          )
        ) : (
          <div className="text-[12px] text-subtle">Plan hesaplanıyor…</div>
        )}
        {reasons.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {reasons.slice(0, 5).map((r) => (
              <span key={r} className="chip">
                {r}
              </span>
            ))}
            {reasons.length > 5 && <span className="chip">+{reasons.length - 5}</span>}
          </div>
        )}
        {s.warnings.length > 0 && (
          <div className="mt-2 space-y-1">
            {s.warnings.slice(0, 2).map((w) => (
              <div key={w} className="flex items-start gap-1.5 text-[11.5px] text-warn/90">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                {w}
              </div>
            ))}
          </div>
        )}
        {!active && s.status_note && <div className="mt-2 text-[11.5px] text-subtle">{s.status_note}</div>}
      </div>

      {active && (
        <div className="mt-auto flex items-center gap-2 border-t border-line px-4 py-3">
          <Button size="sm" variant="ghost" icon={<X className="h-3.5 w-3.5" />} onClick={onDismiss}>
            Yoksay
          </Button>
          <div className="flex-1" />
          <Button size="sm" variant="secondary" icon={<Eye className="h-3.5 w-3.5" />} onClick={onOpen}>
            İncele
          </Button>
          <Button size="sm" variant={s.side === 'LONG' ? 'success' : 'danger'} onClick={onExecute} disabled={!!plan && !plan.ok}>
            {s.side === 'LONG' ? 'Long aç' : 'Short aç'}
          </Button>
        </div>
      )}
      {active && autoMode && plan?.ok && (
        <div className="border-t border-line bg-up/[0.04] px-4 py-2 text-[11.5px] text-up/90">
          Otomatik mod açık: bu fırsat uygunsa sistem tarafından işlenecek.
        </div>
      )}
    </div>
  );
}

export function statusText(st: Signal['status']): string {
  switch (st) {
    case 'ACTIVE':
      return 'Aktif';
    case 'EXECUTED':
      return 'İşleme girildi';
    case 'DISMISSED':
      return 'Yoksayıldı';
    case 'EXPIRED':
      return 'Süresi doldu';
    case 'REJECTED':
      return 'Reddedildi';
    case 'FAILED':
      return 'Başarısız';
    default:
      return st;
  }
}

/** Panel için tek satırlık özet. */
export function SignalRow({ signal: s, onOpen }: { signal: Signal; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-hover/60"
    >
      <ScoreRing score={s.score} size={34} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold text-fg">{s.symbol}</span>
          <SideBadge side={s.side} size="sm" />
        </div>
        <div className="mt-0.5 truncate text-[11.5px] text-subtle">
          {s.strategy_name} · {tfLabel(s.timeframe)}
        </div>
      </div>
      <div className="text-right">
        <div className={clsx('num text-[12.5px] font-semibold', s.ev_r >= 0 ? 'text-up' : 'text-down')}>{fmtR(s.ev_r)}</div>
        <div
          className={clsx('num text-[11px]', s.plan && !s.plan.ok ? 'text-warn' : 'text-subtle')}
          title={s.plan && !s.plan.ok ? s.plan.reason : undefined}
        >
          {s.plan?.ok ? `${s.plan.leverage}x · ${fmtUsd(s.plan.notional, { digits: 0 })}` : s.plan ? 'şu an uygun değil' : 'hesaplanıyor'}
        </div>
      </div>
      <Countdown until={s.expires_at} className="w-[52px] justify-end" />
    </button>
  );
}
