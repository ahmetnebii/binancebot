import clsx from 'clsx';
import { CheckCircle2, Plus, Search, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { ManualOrder, MarketSymbol, OrderPreview, Side } from '../../api/types';
import { useDebounced } from '../../lib/hooks';
import { fmtCompact, fmtLev, fmtPct, fmtPrice, fmtUsd } from '../../lib/format';
import { navigate } from '../../lib/router';
import { useApp } from '../../store/app';
import { TradeChart, type ChartOverlay } from '../charts/TradeChart';
import { Field, NumberInput, Slider } from '../ui/form';
import { Drawer, InfoTip, useConfirm } from '../ui/overlay';
import { Button, Switch } from '../ui/primitives';
import { PlanPanel } from './SignalDrawer';
import { useRiskBounds } from './useRiskBounds';

interface TpRow {
  r: number;
  fraction: number;
}

const DEFAULT_TPS: TpRow[] = [
  { r: 1.5, fraction: 0.4 },
  { r: 3, fraction: 0.3 },
];

function SymbolPicker({
  symbols,
  value,
  onChange,
}: {
  symbols: MarketSymbol[];
  value: string;
  onChange: (s: string) => void;
}) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const on = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', on);
    return () => window.removeEventListener('mousedown', on);
  }, []);
  const list = useMemo(() => {
    const qq = q.trim().toUpperCase();
    return symbols
      .filter((s) => !qq || s.symbol.includes(qq))
      .sort((a, b) => Number(b.in_universe) - Number(a.in_universe) || b.quote_volume - a.quote_volume)
      .slice(0, 60);
  }, [symbols, q]);
  const cur = symbols.find((s) => s.symbol === value);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-11 w-full items-center gap-3 rounded-lg border border-line bg-bg px-3 text-left hover:border-line-strong"
      >
        <Search className="h-4 w-4 text-subtle" />
        <span className="text-[14px] font-semibold text-fg">{value || 'Sembol seç'}</span>
        {cur && (
          <span className="num ml-auto flex items-center gap-2 text-[12px]">
            <span className="text-fg">{fmtPrice(cur.price)}</span>
            <span className={cur.change_pct >= 0 ? 'text-up' : 'text-down'}>{fmtPct(cur.change_pct / 100, { sign: true })}</span>
          </span>
        )}
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-30 mt-1.5 animate-scale-in overflow-hidden rounded-xl border border-line bg-elevated shadow-pop">
          <div className="border-b border-line p-2">
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ara: BTC, SOL…"
              className="num h-8 w-full rounded-md border border-line bg-bg px-2.5 text-[12.5px] text-fg outline-none focus:border-accent"
            />
          </div>
          <div className="max-h-[300px] overflow-y-auto p-1">
            {list.map((s) => (
              <button
                key={s.symbol}
                type="button"
                onClick={() => {
                  onChange(s.symbol);
                  setOpen(false);
                  setQ('');
                }}
                className={clsx(
                  'flex w-full items-center gap-3 rounded-md px-2.5 py-1.5 text-left text-[12.5px] hover:bg-hover',
                  s.symbol === value && 'bg-hover',
                )}
              >
                <span className="w-28 font-semibold text-fg">{s.symbol}</span>
                <span className="num w-24 text-right text-fg">{fmtPrice(s.price)}</span>
                <span className={clsx('num w-16 text-right', s.change_pct >= 0 ? 'text-up' : 'text-down')}>
                  {fmtPct(s.change_pct / 100, { sign: true })}
                </span>
                <span className="num ml-auto text-[11px] text-subtle">Hcm {fmtCompact(s.quote_volume)}</span>
                {!s.in_universe && <span className="text-[10px] text-subtle">evren dışı</span>}
              </button>
            ))}
            {!list.length && <div className="px-3 py-6 text-center text-[12px] text-subtle">Sonuç yok</div>}
          </div>
        </div>
      )}
    </div>
  );
}

export function ManualOrderTicket({
  open,
  onClose,
  initialSymbol,
}: {
  open: boolean;
  onClose: () => void;
  initialSymbol?: string;
}) {
  const status = useApp((s) => s.status);
  const bounds = useRiskBounds();
  const [symbols, setSymbols] = useState<MarketSymbol[]>([]);
  const [symbol, setSymbol] = useState(initialSymbol ?? '');
  const [side, setSide] = useState<Side>('LONG');
  const [stop, setStop] = useState<number | null>(null);
  const [useDefaultTps, setUseDefaultTps] = useState(true);
  const [tps, setTps] = useState<TpRow[]>(DEFAULT_TPS);
  const [riskPct, setRiskPct] = useState<number | null>(null);
  const [manualLev, setManualLev] = useState(false);
  const [lev, setLev] = useState<number | null>(null);
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const { confirm, node } = useConfirm();

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const load = () =>
      api
        .symbols()
        .then((list) => {
          if (!alive) return;
          setSymbols(list);
          setSymbol((cur) => cur || initialSymbol || list.find((s) => s.symbol === 'BTCUSDT')?.symbol || list[0]?.symbol || '');
        })
        .catch(() => undefined);
    void load();
    const t = window.setInterval(load, 5000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [open, initialSymbol]);

  useEffect(() => {
    if (open) {
      setRiskPct(bounds.base);
      setPreview(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const cur = symbols.find((s) => s.symbol === symbol);
  const price = cur?.price ?? null;

  // Sembol/yön değişince varsayılan stop: %2
  useEffect(() => {
    if (price) setStop(Number((side === 'LONG' ? price * 0.98 : price * 1.02).toPrecision(8)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, side, !!price]);

  const order: ManualOrder | null =
    symbol && stop
      ? {
          symbol,
          side,
          stop_price: stop,
          take_profits: useDefaultTps ? undefined : tps.map((t) => ({ r: t.r, fraction: t.fraction })),
          risk_pct: riskPct ?? undefined,
          leverage: manualLev && lev ? lev : undefined,
        }
      : null;
  const dOrder = useDebounced(order, 300);
  const orderKey = JSON.stringify(dOrder);

  useEffect(() => {
    if (!open || !dOrder) return;
    let alive = true;
    setPreviewLoading(true);
    api
      .preview(dOrder)
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setPreviewErr(null);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setPreview(null);
        setPreviewErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => alive && setPreviewLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, orderKey]);

  const stopInvalid = !!(price && stop && (side === 'LONG' ? stop >= price : stop <= price));

  const overlay: ChartOverlay | null = useMemo(() => {
    if (!price || !stop || stopInvalid) return null;
    const entry = preview?.entry ?? price;
    const risk = Math.abs(entry - stop);
    const sign = side === 'LONG' ? 1 : -1;
    const tpPrices = preview?.take_profits?.length
      ? preview.take_profits.map((t, i) => ({ price: t.price, label: `TP${i + 1}`, filled: false }))
      : (useDefaultTps ? DEFAULT_TPS : tps).map((t, i) => ({ price: entry + sign * t.r * risk, label: `TP${i + 1}`, filled: false }));
    return {
      side,
      entry,
      stop,
      takeProfits: tpPrices,
      liq: preview?.plan.ok ? preview.plan.liq_price : null,
      startTime: Date.now() - 60 * 60 * 1000,
      preview: true,
    };
  }, [price, stop, side, preview, useDefaultTps, tps, stopInvalid]);

  const submit = async () => {
    if (!order) return;
    const p = preview?.plan;
    const ok = await confirm({
      title: `${symbol} ${side === 'LONG' ? 'long' : 'short'} açılsın mı?`,
      message: p?.ok
        ? `${fmtLev(p.leverage)} kaldıraç · ${fmtUsd(p.notional)} USDT büyüklük · ${fmtUsd(p.risk_usdt)} USDT risk. Stop ve hedef emirleri otomatik yerleştirilir.`
        : 'Plan doğrulanamadı.',
      confirmText: side === 'LONG' ? 'Long aç' : 'Short aç',
      tone: status?.env === 'live' ? 'danger' : 'success',
      typeToConfirm: status?.env === 'live' ? 'CANLI' : undefined,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const pos = await withToast(api.manualOrder(order), 'Emir gönderilemedi');
      useApp.getState().upsertPosition(pos); // "Pozisyon açıldı" bildirimi sunucudan gelir
      onClose();
      navigate(`/pozisyon/${pos.id}`);
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };

  const quickStop = (pct: number) => {
    if (!price) return;
    setStop(Number((side === 'LONG' ? price * (1 - pct) : price * (1 + pct)).toPrecision(8)));
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Yeni işlem"
      subtitle="Manuel emir — büyüklük ve kaldıraç risk motoru tarafından hesaplanır, stop zorunludur"
      footer={
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[12px] text-muted">
            {status?.env === 'live' ? (
              <span className="text-down">CANLI hesapta gerçek emir gönderilecek.</span>
            ) : status?.env === 'testnet' ? (
              'Testnet hesabına emir gönderilecek.'
            ) : (
              'Simülasyon: emir sanal olarak gerçekleşir.'
            )}
          </span>
          <div className="flex-1" />
          <Button variant="ghost" onClick={onClose}>
            Vazgeç
          </Button>
          <Button
            size="lg"
            variant={side === 'LONG' ? 'success' : 'danger'}
            disabled={!order || stopInvalid || !preview?.plan.ok}
            loading={busy}
            onClick={submit}
            icon={<CheckCircle2 className="h-4 w-4" />}
          >
            {side === 'LONG' ? 'Long aç' : 'Short aç'}
          </Button>
        </div>
      }
    >
      {node}
      <div className="grid gap-4 p-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          <SymbolPicker symbols={symbols} value={symbol} onChange={setSymbol} />
          <div className="card overflow-hidden">
            {symbol ? (
              <TradeChart key={symbol} symbol={symbol} interval="15m" overlay={overlay} height={430} />
            ) : (
              <div className="flex h-[470px] items-center justify-center text-[12.5px] text-subtle">Sembol seçin</div>
            )}
          </div>
        </div>
        <div className="space-y-4">
          <div className="card space-y-4 p-4">
            <div className="grid grid-cols-2 gap-1 rounded-xl border border-line bg-bg p-1">
              {(['LONG', 'SHORT'] as Side[]).map((sd) => (
                <button
                  key={sd}
                  type="button"
                  onClick={() => setSide(sd)}
                  className={clsx(
                    'h-10 rounded-lg text-[13px] font-bold tracking-wide transition-colors',
                    side === sd
                      ? sd === 'LONG'
                        ? 'bg-up text-[#04140D] shadow-card'
                        : 'bg-down text-white shadow-card'
                      : 'text-muted hover:text-fg',
                  )}
                >
                  {sd === 'LONG' ? 'LONG · Al' : 'SHORT · Sat'}
                </button>
              ))}
            </div>

            <div className="flex items-center justify-between rounded-lg border border-line px-3 py-2">
              <span className="text-[12px] text-muted">Anlık fiyat</span>
              <span className="num text-[14px] font-semibold text-fg">{fmtPrice(price)}</span>
            </div>

            <Field
              label={
                <>
                  Stop-loss fiyatı <span className="text-down">*</span>
                </>
              }
              error={stopInvalid ? (side === 'LONG' ? 'Long için stop fiyatın altında olmalı' : 'Short için stop fiyatın üstünde olmalı') : undefined}
              hint={stop && price && !stopInvalid ? `Fiyata uzaklık ${fmtPct(Math.abs(stop - price) / price)}` : undefined}
            >
              <NumberInput value={stop} onChange={setStop} min={0} decimals={8} />
              <div className="mt-2 grid grid-cols-4 gap-1.5">
                {[0.01, 0.02, 0.03, 0.05].map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => quickStop(p)}
                    className="num h-7 rounded-md border border-line bg-elevated text-[11.5px] text-muted hover:border-line-strong hover:text-fg"
                  >
                    {side === 'LONG' ? '−' : '+'}%{p * 100}
                  </button>
                ))}
              </div>
            </Field>

            <div className="rounded-lg border border-line px-3 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[12.5px] font-medium text-fg">Varsayılan hedef merdiveni</div>
                  <div className="text-[11.5px] text-subtle">1,5R'de %40 · 3R'de %30 · kalan iz süren stop</div>
                </div>
                <Switch checked={useDefaultTps} onChange={setUseDefaultTps} size="sm" />
              </div>
              {!useDefaultTps && (
                <div className="mt-3 space-y-2">
                  {tps.map((t, i) => (
                    <div key={i} className="grid grid-cols-[auto_1fr_1fr_auto] items-center gap-2">
                      <span className="num w-8 text-[11.5px] text-subtle">TP{i + 1}</span>
                      <NumberInput value={t.r} onChange={(v) => setTps(tps.map((x, j) => (j === i ? { ...x, r: v ?? x.r } : x)))} suffix="R" min={0.2} max={20} decimals={2} />
                      <NumberInput
                        value={t.fraction}
                        onChange={(v) => setTps(tps.map((x, j) => (j === i ? { ...x, fraction: v ?? x.fraction } : x)))}
                        scale={100}
                        suffix="%"
                        min={0.05}
                        max={1}
                        decimals={0}
                      />
                      <button
                        type="button"
                        onClick={() => setTps(tps.filter((_, j) => j !== i))}
                        className="rounded-md p-1.5 text-subtle hover:bg-hover hover:text-down"
                        aria-label="Hedefi kaldır"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  {tps.length < 3 && (
                    <Button size="xs" variant="ghost" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setTps([...tps, { r: (tps[tps.length - 1]?.r ?? 1) + 1.5, fraction: 0.3 }])}>
                      Hedef ekle
                    </Button>
                  )}
                  <div className="text-[11px] text-subtle">
                    Toplam %{Math.round(tps.reduce((a, t) => a + t.fraction, 0) * 100)} — kalan kısım iz süren stop ile yönetilir.
                  </div>
                </div>
              )}
            </div>

            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="flex items-center gap-1 text-[12px] font-medium text-muted">
                  İşlem riski
                  <InfoTip>Stop tetiklenirse kaybedilecek tutarın özsermayeye oranı.</InfoTip>
                </span>
                <span className="num text-[13px] font-semibold text-fg">{fmtPct(riskPct ?? bounds.base)}</span>
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
                  <NumberInput value={lev} onChange={(v) => setLev(v === null ? null : Math.round(v))} min={1} max={125} step={1} suffix="x" decimals={0} allowEmpty placeholder="ör. 5" />
                </div>
              )}
            </div>
          </div>
          {previewErr && !previewLoading ? (
            <div className="rounded-xl border border-down/30 bg-down/[0.07] px-4 py-3 text-[12.5px] text-down">{previewErr}</div>
          ) : (
            <PlanPanel preview={preview} loading={previewLoading} stop={stop} />
          )}
        </div>
      </div>
    </Drawer>
  );
}
