import clsx from 'clsx';
import {
  ArrowLeft,
  Ban,
  CheckCircle2,
  CircleDollarSign,
  Clock,
  Crosshair,
  Flag,
  LogIn,
  MoveVertical,
  Percent,
  ShieldCheck,
  Target,
  TrendingUp,
  XCircle,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { api, withToast } from '../api/client';
import type { Position, PositionDetail as PD, PositionEvent, Projection } from '../api/types';
import { TradeChart } from '../components/charts/TradeChart';
import { closeReasonText, positionOverlay, strategyTone } from '../components/trade/helpers';
import { SourceIcon } from '../components/trade/PositionViews';
import { Field, NumberInput } from '../components/ui/form';
import { InfoTip, Modal, useConfirm } from '../components/ui/overlay';
import { Badge, Button, Card, CardHeader, Empty, SideBadge, Skeleton } from '../components/ui/primitives';
import { Pnl, positionRBar, priceToR } from '../components/ui/viz';
import { liqDistFrac, roeFrac, stopDistFrac } from '../lib/derive';
import { useInterval, useNow } from '../lib/hooks';
import {
  barsToDuration,
  fmtCountdown,
  fmtDateTime,
  fmtDuration,
  fmtLev,
  fmtPct,
  fmtPrice,
  fmtQty,
  fmtR,
  fmtTimeSec,
  fmtUsd,
  pnlClass,
  tfLabel,
} from '../lib/format';
import { navigate } from '../lib/router';
import { toast, useApp } from '../store/app';

function ProbBar({ p, label, hint }: { p: number | null; label: string; hint?: ReactNode }) {
  return (
    <div>
      <div className="flex items-center justify-between text-[12px]">
        <span className="flex items-center gap-1 text-muted">
          {label}
          {hint && <InfoTip>{hint}</InfoTip>}
        </span>
        <span className="num text-[13px] font-semibold text-fg">{p != null ? fmtPct(p, { digits: 0 }) : '—'}</span>
      </div>
      <div className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-elevated">
        {p != null && (
          <>
            <div className="h-full bg-up transition-all duration-700" style={{ width: `${Math.max(0, Math.min(1, p)) * 100}%` }} />
            <div className="h-full flex-1 bg-down/70" />
          </>
        )}
      </div>
      <div className="mt-1 flex justify-between text-[10.5px] text-subtle">
        <span>hedef önce</span>
        <span>stop önce</span>
      </div>
    </div>
  );
}

function ProjectionCard({ proj, position }: { proj: Projection | null | undefined; position: Position }) {
  if (position.status !== 'OPEN') return null;
  return (
    <Card className="overflow-hidden">
      <CardHeader
        title={
          <span className="flex items-center gap-1.5">
            Tahmini ilerleme
            <InfoTip>
              Bu değerler, fiyatın son dönem volatilitesine dayanan istatistiksel tahminlerdir (rastgele yürüyüş modeli). Grafikteki mor koni
              olası fiyat aralığını (1σ ≈ %68, 2σ ≈ %95), kesikli çizgi mevcut ivme sürerse izlenecek yolu gösterir. Garanti değildir.
            </InfoTip>
          </span>
        }
        icon={<TrendingUp className="h-4 w-4 text-proj" />}
        right={proj?.target_label ? <Badge tone="proj">{proj.target_label}</Badge> : undefined}
      />
      {proj ? (
        <div className="space-y-4 px-4 pb-4">
          <ProbBar
            p={proj.p_target_first}
            label="Hedefe stop'tan önce ulaşma"
            hint="Yönsüz model: fiyatın hedefe ve stop'a olan uzaklıklarının oranından hesaplanır."
          />
          <ProbBar
            p={proj.p_target_first_momentum}
            label="İvme varsayımıyla"
            hint="Son barların ortalama getirisi (ivme) sürerse hedefe önce ulaşma olasılığı."
          />
          <dl className="divide-y divide-line border-t border-line">
            <div className="kv">
              <dt className="flex items-center gap-1">
                Tahmini sonuçlanma süresi <InfoTip>Hedef veya stop'tan birine ulaşılana kadar beklenen süre (yönsüz model).</InfoTip>
              </dt>
              <dd>{proj.expected_bars != null ? `~${barsToDuration(proj.expected_bars, proj.timeframe)}` : '—'}</dd>
            </div>
            <div className="kv">
              <dt>Mevcut ivmeyle hedefe</dt>
              <dd className={proj.eta_bars_momentum != null ? 'text-proj' : 'text-subtle'}>
                {proj.eta_bars_momentum != null ? `~${barsToDuration(proj.eta_bars_momentum, proj.timeframe)}` : 'ivme hedef yönünde değil'}
              </dd>
            </div>
            <div className="kv">
              <dt>Sıradaki hedef</dt>
              <dd>{fmtPrice(proj.target_price)}</dd>
            </div>
            <div className="kv">
              <dt>Bar başına volatilite (σ)</dt>
              <dd>
                {fmtPct(proj.sigma_bar)} <span className="text-subtle">/ {tfLabel(proj.timeframe)}</span>
              </dd>
            </div>
            <div className="kv">
              <dt>Projeksiyon ufku</dt>
              <dd>{barsToDuration(proj.horizon_bars, proj.timeframe)}</dd>
            </div>
          </dl>
        </div>
      ) : (
        <div className="px-4 pb-4 text-[12px] text-subtle">Projeksiyon hesaplanıyor…</div>
      )}
    </Card>
  );
}

const EVENT_ICON: Record<string, { icon: ReactNode; cls: string }> = {
  open: { icon: <LogIn className="h-3.5 w-3.5" />, cls: 'text-accent bg-accent/10 border-accent/30' },
  tp: { icon: <Target className="h-3.5 w-3.5" />, cls: 'text-up bg-up/10 border-up/30' },
  sl: { icon: <XCircle className="h-3.5 w-3.5" />, cls: 'text-down bg-down/10 border-down/30' },
  stop_move: { icon: <MoveVertical className="h-3.5 w-3.5" />, cls: 'text-warn bg-warn/10 border-warn/30' },
  breakeven: { icon: <ShieldCheck className="h-3.5 w-3.5" />, cls: 'text-up bg-up/10 border-up/30' },
  trailing: { icon: <Crosshair className="h-3.5 w-3.5" />, cls: 'text-proj bg-proj/10 border-proj/30' },
  partial_close: { icon: <Percent className="h-3.5 w-3.5" />, cls: 'text-fg bg-elevated border-line-strong' },
  close: { icon: <Flag className="h-3.5 w-3.5" />, cls: 'text-fg bg-elevated border-line-strong' },
  funding: { icon: <CircleDollarSign className="h-3.5 w-3.5" />, cls: 'text-muted bg-elevated border-line' },
  warning: { icon: <Ban className="h-3.5 w-3.5" />, cls: 'text-warn bg-warn/10 border-warn/30' },
  info: { icon: <Clock className="h-3.5 w-3.5" />, cls: 'text-muted bg-elevated border-line' },
};

function Timeline({ events }: { events: PositionEvent[] }) {
  const list = [...events].sort((a, b) => b.ts - a.ts);
  if (!list.length) return <div className="px-4 pb-4 text-[12px] text-subtle">Olay yok</div>;
  return (
    <ol className="relative px-4 pb-3">
      <span className="absolute bottom-5 left-[29px] top-2 w-px bg-line" />
      {list.map((e, i) => {
        const meta = EVENT_ICON[e.kind] ?? EVENT_ICON.info;
        return (
          <li key={`${e.ts}-${i}`} className="relative flex gap-3 py-2">
            <span className={clsx('relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border', meta.cls)}>{meta.icon}</span>
            <div className="min-w-0 flex-1 pt-0.5">
              <div className="text-[12.5px] text-fg">{e.message}</div>
              <div className="num mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-subtle">
                <span>{fmtDateTime(e.ts)}</span>
                {e.price != null && <span>@ {fmtPrice(e.price)}</span>}
                {e.qty != null && <span>miktar {fmtQty(e.qty)}</span>}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function StopModal({ p, open, onClose }: { p: Position; open: boolean; onClose: () => void }) {
  const [price, setPrice] = useState<number | null>(p.stop_price);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setPrice(p.stop_price);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const mark = p.mark_price ?? p.entry_price;
  const invalid = price == null || (p.side === 'LONG' ? price >= mark : price <= mark);
  const newR = price != null ? priceToR(p, price) : null;
  const lockedPnl = price != null ? (price - p.entry_price) * (p.side === 'LONG' ? 1 : -1) * p.qty : null;
  const save = async () => {
    if (price == null || invalid) return;
    setBusy(true);
    try {
      const upd = await withToast(api.moveStop(p.id, price), 'Stop güncellenemedi');
      useApp.getState().upsertPosition(upd);
      toast.success('Stop güncellendi', `${p.symbol} yeni stop ${fmtPrice(price)}`);
      onClose();
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Stop-loss'u düzenle"
      subtitle="Borsadaki stop emri önce yenisi konularak, ardından eskisi iptal edilerek güncellenir; pozisyon hiçbir an korumasız kalmaz."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Vazgeç
          </Button>
          <Button variant="primary" onClick={save} loading={busy} disabled={invalid}>
            Stop'u güncelle
          </Button>
        </>
      }
    >
      <div className="space-y-3 pb-2">
        <Field
          label="Yeni stop fiyatı"
          error={invalid ? (p.side === 'LONG' ? 'Long için stop anlık fiyatın altında olmalı' : 'Short için stop anlık fiyatın üstünde olmalı') : undefined}
        >
          <NumberInput value={price} onChange={setPrice} decimals={8} min={0} />
        </Field>
        <div className="grid grid-cols-2 gap-2 text-[12px]">
          <Button size="sm" variant="secondary" onClick={() => setPrice(p.entry_price)}>
            Başa-baş ({fmtPrice(p.entry_price)})
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setPrice(p.initial_stop)}>
            İlk stop ({fmtPrice(p.initial_stop)})
          </Button>
        </div>
        <dl className="divide-y divide-line rounded-lg border border-line px-3">
          <div className="kv">
            <dt>Anlık fiyat</dt>
            <dd>{fmtPrice(mark)}</dd>
          </div>
          <div className="kv">
            <dt>Yeni stop R karşılığı</dt>
            <dd className={pnlClass(newR)}>{fmtR(newR)}</dd>
          </div>
          <div className="kv">
            <dt>Stop'ta kilitlenen K/Z (kalan miktar)</dt>
            <dd className={pnlClass(lockedPnl)}>{fmtUsd(lockedPnl, { sign: true })} USDT</dd>
          </div>
        </dl>
      </div>
    </Modal>
  );
}

function TpModal({ p, open, onClose }: { p: Position; open: boolean; onClose: () => void }) {
  const [rows, setRows] = useState(p.take_profits.map((t) => ({ price: t.price, fraction: t.fraction, filled: t.filled })));
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setRows(p.take_profits.map((t) => ({ price: t.price, fraction: t.fraction, filled: t.filled })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const mark = p.mark_price ?? p.entry_price;
  const pending = rows.filter((r) => !r.filled);
  const invalid = pending.some((r) => (p.side === 'LONG' ? r.price <= mark : r.price >= mark));
  const save = async () => {
    setBusy(true);
    try {
      const upd = await withToast(
        api.setTakeProfits(
          p.id,
          pending.map((r) => ({ price: r.price, fraction: r.fraction })),
        ),
        'Hedefler güncellenemedi',
      );
      useApp.getState().upsertPosition(upd);
      toast.success('Hedefler güncellendi');
      onClose();
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-xl"
      title="Kâr hedeflerini düzenle"
      subtitle="Dolmuş hedefler değiştirilemez. Oranlar ilk pozisyon miktarına göredir; kalan kısım iz süren stop ile yönetilir."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Vazgeç
          </Button>
          <Button variant="primary" onClick={save} loading={busy} disabled={invalid || !pending.length}>
            Hedefleri kaydet
          </Button>
        </>
      }
    >
      <div className="space-y-2 pb-2">
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[48px_1fr_110px_70px] items-center gap-2">
            <span className="num text-[12px] text-muted">TP{i + 1}</span>
            <NumberInput
              value={r.price}
              disabled={r.filled}
              onChange={(v) => setRows(rows.map((x, j) => (j === i ? { ...x, price: v ?? x.price } : x)))}
              decimals={8}
              min={0}
            />
            <NumberInput
              value={r.fraction}
              disabled={r.filled}
              scale={100}
              suffix="%"
              decimals={0}
              min={0.05}
              max={1}
              onChange={(v) => setRows(rows.map((x, j) => (j === i ? { ...x, fraction: v ?? x.fraction } : x)))}
            />
            <span className={clsx('num text-right text-[11.5px]', r.filled ? 'text-up' : 'text-subtle')}>
              {r.filled ? 'doldu ✓' : fmtR(priceToR(p, r.price), 1)}
            </span>
          </div>
        ))}
        {invalid && <div className="text-[11.5px] text-down">Bekleyen hedefler anlık fiyatın {p.side === 'LONG' ? 'üstünde' : 'altında'} olmalı.</div>}
      </div>
    </Modal>
  );
}

export function PositionDetail({ id }: { id: string }) {
  const live = useApp((s) => s.openPositions.find((p) => p.id === id));
  const rev = useApp((s) => s.positionRev[id] ?? 0);
  const [detail, setDetail] = useState<PD | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopOpen, setStopOpen] = useState(false);
  const [tpOpen, setTpOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const { confirm, node } = useConfirm();
  const now = useNow(1000);

  const load = async () => {
    try {
      const d = await api.position(id);
      setDetail(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => {
    setDetail(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    if (rev > 0) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rev]);
  useInterval(() => void load(), detail?.position.status === 'OPEN' ? 15000 : null);

  const p: Position | null = live ?? detail?.position ?? null;
  const proj = detail?.projection;

  const overlay = useMemo(
    () => (p ? positionOverlay(p, proj) : null),
    // Yalnızca grafiği etkileyen alanlar değişince yeniden hesapla
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [p?.id, p?.stop_price, p?.entry_price, p?.liq_price, p?.status, p?.closed_at, JSON.stringify(p?.take_profits), p?.events.length, proj],
  );

  if (error && !p)
    return (
      <div className="card">
        <Empty icon={<XCircle className="h-5 w-5" />} title="Pozisyon yüklenemedi" action={<Button onClick={() => navigate('/pozisyonlar')}>Pozisyonlara dön</Button>}>
          {error}
        </Empty>
      </div>
    );
  if (!p)
    return (
      <div className="space-y-4">
        <Skeleton className="h-14 w-full rounded-xl" />
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Skeleton className="h-[600px] rounded-xl" />
          <Skeleton className="h-[600px] rounded-xl" />
        </div>
      </div>
    );

  const isOpen = p.status === 'OPEN';
  const stopR = priceToR(p, p.stop_price);
  const beDone = stopR >= -0.001;
  const sd = stopDistFrac(p);
  const ld = liqDistFrac(p);
  const roe = roeFrac(p);
  const rNow = isOpen ? p.r_now : p.r_final ?? p.r_now;

  const act = async (key: string, fn: () => Promise<Position>, okMsg: string | null) => {
    setBusy(key);
    try {
      const upd = await withToast(fn(), 'İşlem başarısız');
      useApp.getState().upsertPosition(upd);
      if (okMsg) toast.success(okMsg, `${upd.symbol}`);
      void load();
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const closePos = async (fraction: number) => {
    const ok = await confirm({
      title: fraction >= 1 ? `${p.symbol} pozisyonu kapatılsın mı?` : `${p.symbol} pozisyonunun %${Math.round(fraction * 100)}'i kapatılsın mı?`,
      message:
        fraction >= 1
          ? `Kalan ${fmtQty(p.qty)} miktar piyasa emriyle kapatılacak. Anlık K/Z: ${fmtUsd(p.pnl_total, { sign: true })} USDT.`
          : `Kalan miktarın %${Math.round(fraction * 100)}'i piyasa emriyle kapatılır; stop ve hedefler kalan miktara göre güncellenir.`,
      confirmText: fraction >= 1 ? 'Pozisyonu kapat' : 'Kısmi kapat',
      tone: 'danger',
    });
    if (!ok) return;
    // Tam kapanışta bildirim sunucudan gelir
    await act(`close${fraction}`, () => api.closePosition(p.id, fraction), fraction >= 1 ? null : 'Kısmi kapanış yapıldı');
  };

  const breakeven = async () => {
    const ok = await confirm({
      title: "Stop başa-başa çekilsin mi?",
      message: `Stop giriş fiyatına (${fmtPrice(p.entry_price)}) taşınır; bu noktadan sonra işlem zararla kapanmaz (komisyon ve kayma hariç).`,
      confirmText: 'Başa-başa çek',
      tone: 'primary',
    });
    if (!ok) return;
    await act('be', () => api.breakeven(p.id), 'Stop başa-başa çekildi');
  };

  return (
    <div className="space-y-4">
      {node}
      {/* Başlık */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => navigate(isOpen ? '/pozisyonlar' : '/pozisyonlar/kapanan')}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface text-muted hover:bg-hover hover:text-fg"
          aria-label="Geri"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-[22px] font-semibold tracking-tight text-fg">{p.symbol}</h1>
            <SideBadge side={p.side} />
            <Badge tone="accent">{fmtLev(p.leverage)} izole</Badge>
            <Badge tone={strategyTone(p.strategy)}>{p.strategy_name}</Badge>
            <SourceIcon source={p.source} />
            {isOpen ? (
              <Badge tone="up" dot>
                Açık
              </Badge>
            ) : (
              <Badge tone="neutral">Kapandı · {closeReasonText(p.close_reason)}</Badge>
            )}
            {p.trailing_active && isOpen && <Badge tone="proj">İz süren stop aktif</Badge>}
          </div>
          <div className="num mt-0.5 text-[12px] text-subtle">
            Açılış {fmtDateTime(p.opened_at)} · {isOpen ? `${fmtDuration(now - p.opened_at)} açık` : `Kapanış ${fmtDateTime(p.closed_at)} · süre ${fmtDuration((p.closed_at ?? now) - p.opened_at)}`} ·{' '}
            {tfLabel(p.timeframe)} strateji zaman dilimi
          </div>
        </div>
        <div className="flex-1" />
        {isOpen && (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" icon={<ShieldCheck className="h-3.5 w-3.5" />} disabled={beDone} loading={busy === 'be'} onClick={breakeven}>
              Başa-baş
            </Button>
            <Button size="sm" variant="secondary" icon={<MoveVertical className="h-3.5 w-3.5" />} onClick={() => setStopOpen(true)}>
              Stop düzenle
            </Button>
            <Button size="sm" variant="secondary" icon={<Target className="h-3.5 w-3.5" />} onClick={() => setTpOpen(true)}>
              Hedefler
            </Button>
            <Button size="sm" variant="outline-danger" loading={busy === 'close0.5'} onClick={() => void closePos(0.5)}>
              %50 kapat
            </Button>
            <Button size="sm" variant="danger" icon={<XCircle className="h-3.5 w-3.5" />} loading={busy === 'close1'} onClick={() => void closePos(1)}>
              Kapat
            </Button>
          </div>
        )}
      </div>

      {/* Özet şeridi */}
      <Card className="grid gap-5 p-4 md:grid-cols-[auto_minmax(0,1fr)] md:items-center">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          <div>
            <div className="label">{isOpen ? 'Toplam K/Z' : 'Sonuç'}</div>
            <div className="mt-1">
              <Pnl value={p.pnl_total} size="xl" />
            </div>
            <div className={clsx('num mt-0.5 text-[12.5px]', pnlClass(roe))}>ROE {fmtPct(roe, { sign: true })} · marjine göre</div>
          </div>
          <div>
            <div className="label">R</div>
            <div className={clsx('num mt-1 text-[24px] font-semibold tracking-tight', pnlClass(rNow))}>{fmtR(rNow)}</div>
            <div className="num text-[11.5px] text-subtle">risk {fmtUsd(p.risk_usdt)} USDT</div>
          </div>
          <div>
            <div className="label">{isOpen ? 'Mark' : 'Çıkış'}</div>
            <div className="num mt-1 text-[18px] font-semibold text-fg">{fmtPrice(isOpen ? p.mark_price : p.exit_price)}</div>
            <div className="num text-[11.5px] text-subtle">giriş {fmtPrice(p.entry_price)}</div>
          </div>
        </div>
        <div className="min-w-0 md:pl-4">{positionRBar({ ...p, r_now: rNow })}</div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          <Card className="overflow-hidden">
            <TradeChart symbol={p.symbol} interval={p.timeframe} overlay={overlay} height={560} />
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line px-4 py-2.5 text-[11.5px] text-muted">
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-3.5 rounded-sm bg-up/30 ring-1 ring-up/50" /> Kâr bölgesi
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-3.5 rounded-sm bg-down/30 ring-1 ring-down/50" /> Risk bölgesi
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-3.5 rounded-sm bg-proj/30 ring-1 ring-proj/50" /> Volatilite konisi (1σ / 2σ)
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-0 w-4 border-t-2 border-dashed border-proj" /> Mevcut ivmeyle tahmini yol
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-0 w-4 border-t-2 border-dashed border-warn" /> Likidasyon
              </span>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Kâr hedefleri" icon={<Target className="h-4 w-4" />} subtitle="Oranlar ilk miktara göre" />
              <div className="overflow-x-auto pb-2">
                <table className="w-full text-[12.5px]">
                  <thead className="table-head border-y border-line">
                    <tr>
                      <th>Hedef</th>
                      <th className="!text-right">Fiyat</th>
                      <th className="!text-right">R</th>
                      <th className="!text-right">Oran</th>
                      <th className="!text-right">Durum</th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.take_profits.map((t, i) => {
                      const dist = p.mark_price ? (t.price - p.mark_price) / p.mark_price : null;
                      return (
                        <tr key={i} className="table-row border-b border-line/60">
                          <td className="font-medium text-fg">TP{i + 1}</td>
                          <td className="num text-right">
                            <span className={t.filled ? 'text-up' : 'text-fg'}>{fmtPrice(t.price)}</span>
                            {!t.filled && dist !== null && isOpen && <div className="text-[10.5px] text-subtle">{fmtPct(dist, { sign: true })}</div>}
                          </td>
                          <td className="num text-right text-muted">{fmtR(t.r || priceToR(p, t.price), 1)}</td>
                          <td className="num text-right text-muted">%{Math.round(t.fraction * 100)}</td>
                          <td className="text-right">
                            {t.filled ? (
                              <span className="inline-flex items-center gap-1 text-[11.5px] text-up">
                                <CheckCircle2 className="h-3.5 w-3.5" /> {t.filled_at ? fmtTimeSec(t.filled_at) : 'doldu'}
                              </span>
                            ) : (
                              <span className="text-[11.5px] text-subtle">bekliyor</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                    {!p.take_profits.length && (
                      <tr>
                        <td colSpan={5} className="px-3 py-4 text-center text-[12px] text-subtle">
                          Hedef yok — pozisyon iz süren stop ile yönetiliyor
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
                {p.take_profits.length > 0 && (
                  <div className="px-3 pt-2 text-[11.5px] text-subtle">
                    Kalan %{Math.max(0, Math.round((1 - p.take_profits.reduce((a, t) => a + t.fraction, 0)) * 100))} iz süren stop ile yönetilir.
                  </div>
                )}
              </div>
            </Card>
            <Card>
              <CardHeader title="Olay akışı" icon={<Clock className="h-4 w-4" />} subtitle={`${p.events.length} olay`} />
              <div className="max-h-[340px] overflow-y-auto">
                <Timeline events={p.events} />
              </div>
            </Card>
          </div>
        </div>

        <div className="space-y-4">
          <ProjectionCard proj={proj} position={p} />
          <Card>
            <CardHeader title="Pozisyon detayları" icon={<Crosshair className="h-4 w-4" />} />
            <dl className="divide-y divide-line px-4 pb-3">
              <div className="kv">
                <dt>Giriş fiyatı</dt>
                <dd>{fmtPrice(p.entry_price)}</dd>
              </div>
              <div className="kv">
                <dt>{isOpen ? 'Mark fiyatı' : 'Ortalama çıkış'}</dt>
                <dd>{fmtPrice(isOpen ? p.mark_price : p.exit_price)}</dd>
              </div>
              <div className="kv">
                <dt>Miktar (kalan / ilk)</dt>
                <dd>
                  {fmtQty(p.qty)} <span className="text-subtle">/ {fmtQty(p.initial_qty)}</span>
                </dd>
              </div>
              <div className="kv">
                <dt>Büyüklük (notional)</dt>
                <dd>{fmtUsd(p.notional)} USDT</dd>
              </div>
              <div className="kv">
                <dt>Marjin · kaldıraç</dt>
                <dd>
                  {fmtUsd(p.margin)} <span className="text-subtle">· {fmtLev(p.leverage)}</span>
                </dd>
              </div>
              <div className="kv">
                <dt>Stop-loss</dt>
                <dd>
                  <span className="text-down">{fmtPrice(p.stop_price)}</span>
                  {sd != null && isOpen && <span className="text-subtle"> ({fmtPct(sd)} uzakta)</span>}
                </dd>
              </div>
              <div className="kv">
                <dt>İlk stop</dt>
                <dd className="text-muted">{fmtPrice(p.initial_stop)}</dd>
              </div>
              <div className="kv">
                <dt className="flex items-center gap-1">
                  Likidasyon <InfoTip>İzole marjinde bu fiyata ulaşılırsa pozisyon borsa tarafından kapatılır. Sistem likidasyonu her zaman stop'tan çok uzakta tutar.</InfoTip>
                </dt>
                <dd>
                  <span className="text-warn">{fmtPrice(p.liq_price)}</span>
                  {ld != null && isOpen && <span className="text-subtle"> ({fmtPct(ld)})</span>}
                </dd>
              </div>
              <div className="kv">
                <dt>Gerçekleşen K/Z</dt>
                <dd className={pnlClass(p.realized_pnl)}>{fmtUsd(p.realized_pnl, { sign: true })}</dd>
              </div>
              {isOpen && (
                <div className="kv">
                  <dt>Gerçekleşmemiş K/Z</dt>
                  <dd className={pnlClass(p.unrealized_pnl)}>{fmtUsd(p.unrealized_pnl, { sign: true })}</dd>
                </div>
              )}
              <div className="kv">
                <dt>Komisyon</dt>
                <dd className="text-muted">{fmtUsd(-Math.abs(p.fees), { sign: true })}</dd>
              </div>
              <div className="kv">
                <dt>Funding</dt>
                <dd className={pnlClass(p.funding)}>{fmtUsd(p.funding, { sign: true })}</dd>
              </div>
              <div className="kv">
                <dt className="flex items-center gap-1">
                  MFE / MAE <InfoTip>İşlem süresince görülen en iyi (MFE) ve en kötü (MAE) ara sonuç, R cinsinden.</InfoTip>
                </dt>
                <dd>
                  <span className="text-up">{fmtR(p.mfe_r, 1)}</span> / <span className="text-down">{fmtR(-Math.abs(p.mae_r), 1)}</span>
                </dd>
              </div>
              {isOpen && p.time_stop_at && (
                <div className="kv">
                  <dt className="flex items-center gap-1">
                    Zaman stopu <InfoTip>Bu süre içinde yeterli ilerleme olmazsa pozisyon kapatılır.</InfoTip>
                  </dt>
                  <dd>{fmtCountdown(p.time_stop_at - now)}</dd>
                </div>
              )}
              {!isOpen && (
                <div className="kv">
                  <dt>Kapanış nedeni</dt>
                  <dd>{closeReasonText(p.close_reason)}</dd>
                </div>
              )}
            </dl>
          </Card>
        </div>
      </div>

      {isOpen && (
        <>
          <StopModal p={p} open={stopOpen} onClose={() => setStopOpen(false)} />
          <TpModal p={p} open={tpOpen} onClose={() => setTpOpen(false)} />
        </>
      )}
    </div>
  );
}
