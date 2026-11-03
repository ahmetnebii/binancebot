import clsx from 'clsx';
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineData,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { AlertTriangle, Loader2 } from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { Candle, Projection, Side } from '../../api/types';
import { subscribeKline } from '../../api/ws';
import { barsToDuration, fmtCompact, fmtPct, fmtPrice, priceDecimals, tfSeconds } from '../../lib/format';
import { Segmented } from '../ui/primitives';
import { ConePrimitive, ZonesPrimitive, type ConeDrawPoint } from './primitives';

/** lightweight-charts zamanları UTC gösterir; yerel saat için kaydırıyoruz. */
const TZ_SHIFT = -new Date().getTimezoneOffset() * 60;
const toChart = (sec: number) => (sec + TZ_SHIFT) as UTCTimestamp;

export const CHART_INTERVALS = ['1m', '5m', '15m', '1h', '4h'] as const;

export interface ChartMarker {
  time: number; // ms
  kind: 'entry' | 'tp' | 'sl' | 'close' | 'partial_close' | 'info';
  text: string;
}

export interface ChartOverlay {
  side: Side;
  entry: number;
  stop: number;
  takeProfits: { price: number; label: string; filled: boolean }[];
  liq?: number | null;
  /** Bölgelerin başlangıcı (ms): giriş zamanı ya da sinyal mumu. */
  startTime?: number | null;
  /** Kapanmış pozisyon için bölgelerin bitişi (ms). */
  endTime?: number | null;
  markers?: ChartMarker[];
  projection?: Projection | null;
  breakeven?: boolean;
  preview?: boolean;
}

interface Toggles {
  zones: boolean;
  levels: boolean;
  projection: boolean;
  markers: boolean;
}

const COLORS = {
  bg: '#10141B',
  grid: 'rgba(34,41,54,0.55)',
  text: '#8B95A5',
  border: '#222936',
  up: '#17C987',
  down: '#F04B5B',
  accent: '#5B8CFF',
  warn: '#F5A524',
  proj: '#A78BFA',
  fg: '#E7ECF3',
};

function interp(pts: { t: number; v: number }[], t: number): number {
  if (t <= pts[0].t) return pts[0].v;
  for (let i = 1; i < pts.length; i++) {
    if (t <= pts[i].t) {
      const a = pts[i - 1];
      const b = pts[i];
      const w = (t - a.t) / (b.t - a.t || 1);
      return a.v + (b.v - a.v) * w;
    }
  }
  return pts[pts.length - 1].v;
}

/** Projeksiyonu grafiğin aralığına göre yeniden örnekler (zamanlar gerçek saniye). */
function resampleProjection(proj: Projection, last: Candle, tfSec: number) {
  const s0 = last.time;
  const p0 = last.close;
  const cone = proj.cone.filter((c) => c.time > s0).sort((a, b) => a.time - b.time);
  if (!cone.length) return null;
  const endT = cone[cone.length - 1].time;
  const times: number[] = [];
  for (let t = s0 + tfSec; t <= endT + 0.5 && times.length < 720; t += tfSec) times.push(t);
  if (!times.length) times.push(s0 + tfSec);
  const series = (key: 'up1' | 'dn1' | 'up2' | 'dn2') => [{ t: s0, v: p0 }, ...cone.map((c) => ({ t: c.time, v: c[key] }))];
  const u1 = series('up1');
  const d1 = series('dn1');
  const u2 = series('up2');
  const d2 = series('dn2');
  const points: ConeDrawPoint[] = [{ time: s0, up1: p0, dn1: p0, up2: p0, dn2: p0 }];
  for (const t of times) points.push({ time: t, up1: interp(u1, t), dn1: interp(d1, t), up2: interp(u2, t), dn2: interp(d2, t) });

  const pathSrc = proj.momentum_path.filter((p) => p.time > s0).sort((a, b) => a.time - b.time);
  let path: { time: number; value: number }[] = [];
  if (pathSrc.length) {
    const pp = [{ t: s0, v: p0 }, ...pathSrc.map((p) => ({ t: p.time, v: p.price }))];
    const pEnd = pathSrc[pathSrc.length - 1].time;
    path = [{ time: s0, value: p0 }, ...times.filter((t) => t <= pEnd + 0.5).map((t) => ({ time: t, value: interp(pp, t) }))];
  }

  let target: { time: number; price: number; label: string } | null = null;
  if (proj.target_price && proj.eta_bars_momentum != null && proj.eta_bars_momentum > 0) {
    const projTf = tfSeconds(proj.timeframe);
    const etaT = s0 + proj.eta_bars_momentum * projTf;
    if (etaT <= endT + projTf) {
      // En yakın örnek zamana oturt
      let best = times[0];
      for (const t of times) if (Math.abs(t - etaT) < Math.abs(best - etaT)) best = t;
      target = {
        time: best,
        price: proj.target_price,
        label: `${proj.target_label ?? 'Hedef'} · ~${barsToDuration(proj.eta_bars_momentum, proj.timeframe)}`,
      };
    }
  }
  return { points, path, target };
}

export function TradeChart({
  symbol,
  interval: initialInterval,
  overlay,
  height = 460,
  className,
  onIntervalChange,
  toolbarLeft,
}: {
  symbol: string;
  interval: string;
  overlay?: ChartOverlay | null;
  height?: number;
  className?: string;
  onIntervalChange?: (iv: string) => void;
  toolbarLeft?: ReactNode;
}) {
  const [interval, setIntervalState] = useState(initialInterval);
  const [toggles, setToggles] = useState<Toggles>({ zones: true, levels: true, projection: true, markers: true });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const [legend, setLegend] = useState<Candle | null>(null);
  const [hovering, setHovering] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const linesRef = useRef<{
    u1: ISeriesApi<'Line'>;
    d1: ISeriesApi<'Line'>;
    u2: ISeriesApi<'Line'>;
    d2: ISeriesApi<'Line'>;
    path: ISeriesApi<'Line'>;
  } | null>(null);
  const zonesRef = useRef<ZonesPrimitive | null>(null);
  const coneRef = useRef<ConePrimitive | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const candlesRef = useRef<Candle[]>([]);
  const fittedFutureRef = useRef(false);
  const decimalsRef = useRef(2);
  const previewRef = useRef(false);
  previewRef.current = !!overlay?.preview;

  useEffect(() => setIntervalState(initialInterval), [initialInterval]);

  // Grafik kurulumu
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: COLORS.bg },
        textColor: COLORS.text,
        fontFamily: '"Inter Variable", Inter, system-ui, sans-serif',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderColor: COLORS.border, scaleMargins: { top: 0.08, bottom: 0.2 } },
      timeScale: { borderColor: COLORS.border, timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 8 },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#5D6676', style: LineStyle.Dashed, width: 1, labelBackgroundColor: '#1B212C' },
        horzLine: { color: '#5D6676', style: LineStyle.Dashed, width: 1, labelBackgroundColor: '#1B212C' },
      },
      localization: { locale: 'tr-TR', priceFormatter: (p: number) => fmtPrice(p, decimalsRef.current) },
    });
    const candles = chart.addCandlestickSeries({
      upColor: COLORS.up,
      downColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
      borderVisible: false,
      priceLineColor: '#8B95A5',
      priceLineStyle: LineStyle.Dotted,
    });
    const vol = chart.addHistogramSeries({
      priceScaleId: 'vol',
      priceFormat: { type: 'volume' },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false });

    const lineOpts = {
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    } as const;
    const lines = {
      // 2σ bantları ölçeklemeye katılmaz; mumlar sıkışmasın
      u2: chart.addLineSeries({ ...lineOpts, color: 'rgba(167,139,250,0.35)', lineWidth: 1, lineStyle: LineStyle.Dotted, autoscaleInfoProvider: () => null }),
      d2: chart.addLineSeries({ ...lineOpts, color: 'rgba(167,139,250,0.35)', lineWidth: 1, lineStyle: LineStyle.Dotted, autoscaleInfoProvider: () => null }),
      u1: chart.addLineSeries({ ...lineOpts, color: 'rgba(167,139,250,0.6)', lineWidth: 1, lineStyle: LineStyle.Solid }),
      d1: chart.addLineSeries({ ...lineOpts, color: 'rgba(167,139,250,0.6)', lineWidth: 1, lineStyle: LineStyle.Solid }),
      path: chart.addLineSeries({ ...lineOpts, color: COLORS.proj, lineWidth: 2, lineStyle: LineStyle.Dashed }),
    };

    const zones = new ZonesPrimitive();
    zones.fallbackX = (t: number) => {
      const arr = candlesRef.current;
      if (!arr.length) return 0;
      const real = t - TZ_SHIFT;
      let lo = 0;
      let hi = arr.length - 1;
      if (real <= arr[0].time) return 0;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (arr[mid].time <= real) lo = mid;
        else hi = mid - 1;
      }
      return chart.timeScale().logicalToCoordinate(lo as never) ?? 0;
    };
    const cone = new ConePrimitive();
    candles.attachPrimitive(zones);
    candles.attachPrimitive(cone);

    const onMove = (param: MouseEventParams<Time>) => {
      if (!param.time || !param.point) {
        setHovering(false);
        return;
      }
      const d = param.seriesData.get(candles) as CandlestickData<Time> | undefined;
      if (!d) return;
      const realTime = (d.time as number) - TZ_SHIFT;
      const c = candlesRef.current.find((x) => x.time === realTime);
      setLegend(c ?? { time: realTime, open: d.open, high: d.high, low: d.low, close: d.close, volume: 0 });
      setHovering(true);
    };
    chart.subscribeCrosshairMove(onMove);

    chartRef.current = chart;
    candleRef.current = candles;
    volRef.current = vol;
    linesRef.current = lines;
    zonesRef.current = zones;
    coneRef.current = cone;
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volRef.current = null;
      linesRef.current = null;
      zonesRef.current = null;
      coneRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  // Veri yükleme + canlı güncelleme
  useEffect(() => {
    let alive = true;
    const candles = candleRef.current;
    const vol = volRef.current;
    const chart = chartRef.current;
    if (!candles || !vol || !chart) return;
    setLoading(true);
    setError(null);
    fittedFutureRef.current = false;
    candlesRef.current = [];
    if (linesRef.current) for (const s of Object.values(linesRef.current)) s.setData([]);
    coneRef.current?.setData(null);

    const toCandle = (c: Candle): CandlestickData<UTCTimestamp> => ({
      time: toChart(c.time),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    });
    const toVol = (c: Candle) => ({
      time: toChart(c.time),
      value: c.volume,
      color: c.close >= c.open ? 'rgba(23,201,135,0.28)' : 'rgba(240,75,91,0.28)',
    });

    api
      .klines(symbol, interval, 500)
      .then((data) => {
        if (!alive) return;
        const sorted = [...data].sort((a, b) => a.time - b.time);
        candlesRef.current = sorted;
        const ref = sorted.length ? sorted[sorted.length - 1].close : 1;
        const dec = priceDecimals(ref);
        decimalsRef.current = dec;
        candles.applyOptions({ priceFormat: { type: 'price', precision: dec, minMove: Number((10 ** -dec).toFixed(dec)) } });
        candles.setData(sorted.map(toCandle));
        vol.setData(sorted.map(toVol));
        const n = sorted.length;
        const pad = previewRef.current ? 26 : 6;
        chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - (previewRef.current ? 110 : 130)), to: n + pad });
        setLegend(sorted[n - 1] ?? null);
        setLoading(false);
        setDataVersion((v) => v + 1);
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : 'Grafik verisi alınamadı');
        setLoading(false);
      });

    const unsub = subscribeKline(symbol, interval, (c) => {
      const arr = candlesRef.current;
      if (!arr.length) return;
      const last = arr[arr.length - 1];
      if (c.time < last.time) return;
      if (c.time === last.time) arr[arr.length - 1] = c;
      else {
        arr.push(c);
        if (arr.length > 1500) arr.shift();
      }
      try {
        candles.update(toCandle(c));
        vol.update(toVol(c));
      } catch {
        /* sıra dışı güncelleme */
      }
      setLegend((prev) => (prev && prev.time > c.time ? prev : c));
      if (c.time !== last.time) setDataVersion((v) => v + 1);
    });
    return () => {
      alive = false;
      unsub();
    };
  }, [symbol, interval]);

  // Seviyeler, bölgeler, işaretler
  useEffect(() => {
    const candles = candleRef.current;
    const zones = zonesRef.current;
    if (!candles || !zones) return;
    for (const pl of priceLinesRef.current) candles.removePriceLine(pl);
    priceLinesRef.current = [];
    if (!overlay) {
      zones.setData(null);
      candles.setMarkers([]);
      return;
    }
    const sign = overlay.side === 'LONG' ? 1 : -1;
    const entry = overlay.entry;
    const pct = (p: number) => fmtPct((p - entry) / entry, { sign: true });
    const tfSec = tfSeconds(interval);
    const arr = candlesRef.current;

    if (toggles.levels) {
      const add = (price: number, color: string, title: string, style: LineStyle, width: 1 | 2 = 1) => {
        if (!Number.isFinite(price) || price <= 0) return;
        priceLinesRef.current.push(
          candles.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title }),
        );
      };
      add(entry, 'rgba(231,236,243,0.75)', 'Giriş', LineStyle.Dashed);
      add(overlay.stop, COLORS.down, overlay.breakeven ? 'SL · başa-baş' : `SL ${pct(overlay.stop)}`, LineStyle.Solid, 2);
      overlay.takeProfits.forEach((tp) => {
        add(
          tp.price,
          tp.filled ? 'rgba(23,201,135,0.45)' : COLORS.up,
          `${tp.label}${tp.filled ? ' ✓' : ''} ${pct(tp.price)}`,
          tp.filled ? LineStyle.Dotted : LineStyle.Dashed,
        );
      });
      if (overlay.liq && overlay.liq > 0) add(overlay.liq, COLORS.warn, 'Likidasyon', LineStyle.LargeDashed);
    }

    if (toggles.zones) {
      const pending = overlay.takeProfits.filter((t) => !t.filled);
      const allTp = overlay.takeProfits;
      const target = allTp.length
        ? allTp.reduce((best, t) => (sign * t.price > sign * best.price ? t : best), allTp[0]).price
        : null;
      const risk = Math.abs(entry - overlay.stop) || 1e-12;
      const rewardR = target !== null ? Math.abs(target - entry) / risk : 0;
      const snap = (ms: number | null | undefined) => {
        if (ms == null || !arr.length) return null;
        const sec = Math.floor(ms / 1000 / tfSec) * tfSec;
        const clamped = Math.max(arr[0].time, sec);
        return toChart(clamped);
      };
      zones.setData({
        side: overlay.side,
        entry,
        stop: overlay.stop,
        target,
        startTime: snap(overlay.startTime ?? null),
        endTime: overlay.endTime ? snap(overlay.endTime) : null,
        riskLabel: overlay.breakeven ? 'Risk yok · başa-baş' : `Risk · ${pct(overlay.stop)}`,
        rewardLabel:
          target !== null
            ? `Hedef · +${rewardR.toLocaleString('tr-TR', { maximumFractionDigits: 1 })}R · ${pct(target)}`
            : '',
        dim: pending.length === 0 && allTp.length > 0,
      });
    } else zones.setData(null);

    if (toggles.markers && overlay.markers && arr.length) {
      const first = arr[0].time;
      const lastT = arr[arr.length - 1].time;
      const ms: SeriesMarker<Time>[] = [];
      for (const m of overlay.markers) {
        const sec = Math.floor(m.time / 1000 / tfSec) * tfSec;
        if (sec < first || sec > lastT) continue;
        const t = toChart(sec);
        if (m.kind === 'entry')
          ms.push({
            time: t,
            position: overlay.side === 'LONG' ? 'belowBar' : 'aboveBar',
            shape: overlay.side === 'LONG' ? 'arrowUp' : 'arrowDown',
            color: COLORS.accent,
            text: m.text,
          });
        else if (m.kind === 'tp')
          ms.push({ time: t, position: overlay.side === 'LONG' ? 'aboveBar' : 'belowBar', shape: 'circle', color: COLORS.up, text: m.text });
        else if (m.kind === 'sl')
          ms.push({ time: t, position: overlay.side === 'LONG' ? 'belowBar' : 'aboveBar', shape: 'circle', color: COLORS.down, text: m.text });
        else if (m.kind === 'close' || m.kind === 'partial_close')
          ms.push({ time: t, position: 'aboveBar', shape: 'square', color: COLORS.fg, text: m.text });
      }
      ms.sort((a, b) => (a.time as number) - (b.time as number));
      candles.setMarkers(ms);
    } else candles.setMarkers([]);
  }, [overlay, toggles.levels, toggles.zones, toggles.markers, dataVersion, interval]);

  // Projeksiyon
  useEffect(() => {
    const lines = linesRef.current;
    const cone = coneRef.current;
    const chart = chartRef.current;
    if (!lines || !cone || !chart) return;
    const arr = candlesRef.current;
    const proj = overlay?.projection;
    if (!toggles.projection || !proj || !arr.length) {
      for (const s of Object.values(lines)) s.setData([]);
      cone.setData(null);
      return;
    }
    const res = resampleProjection(proj, arr[arr.length - 1], tfSeconds(interval));
    if (!res) {
      for (const s of Object.values(lines)) s.setData([]);
      cone.setData(null);
      return;
    }
    const mk = (key: 'up1' | 'dn1' | 'up2' | 'dn2'): LineData<UTCTimestamp>[] =>
      res.points.map((p) => ({ time: toChart(p.time), value: p[key] }));
    lines.u1.setData(mk('up1'));
    lines.d1.setData(mk('dn1'));
    lines.u2.setData(mk('up2'));
    lines.d2.setData(mk('dn2'));
    lines.path.setData(res.path.map((p) => ({ time: toChart(p.time), value: p.value })));
    cone.setData({
      points: res.points.map((p) => ({ ...p, time: toChart(p.time) })),
      target: res.target ? { ...res.target, time: toChart(res.target.time) } : null,
    });
    if (!fittedFutureRef.current) {
      fittedFutureRef.current = true;
      const n = arr.length;
      const future = res.points.length;
      const visibleBars = Math.max(90, Math.min(160, future * 3));
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - visibleBars), to: n + future + 3 });
    }
  }, [overlay?.projection, toggles.projection, dataVersion, interval]);

  const changeInterval = (iv: string) => {
    setIntervalState(iv);
    onIntervalChange?.(iv);
  };

  const shown = legend;
  const prev = useMemo(() => {
    if (!shown) return null;
    const arr = candlesRef.current;
    const i = arr.findIndex((c) => c.time === shown.time);
    return i > 0 ? arr[i - 1] : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, dataVersion]);
  const chg = shown && prev ? (shown.close - prev.close) / prev.close : null;
  const hasProjection = !!overlay?.projection;

  const toggleBtn = (key: keyof Toggles, label: string, color: string, disabled?: boolean) => (
    <button
      type="button"
      disabled={disabled}
      onClick={() => setToggles((t) => ({ ...t, [key]: !t[key] }))}
      className={clsx(
        'inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-[11.5px] font-medium transition-colors disabled:opacity-40',
        toggles[key] && !disabled ? 'border-line-strong bg-elevated text-fg' : 'border-transparent text-subtle hover:text-muted',
      )}
    >
      <span className="h-2 w-2 rounded-sm" style={{ background: toggles[key] && !disabled ? color : '#5D6676' }} />
      {label}
    </button>
  );

  return (
    <div className={clsx('flex flex-col', className)}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        {toolbarLeft}
        <Segmented
          size="xs"
          value={interval}
          onChange={changeInterval}
          options={CHART_INTERVALS.map((iv) => ({ value: iv, label: iv }))}
        />
        <div className="flex-1" />
        {overlay && (
          <div className="flex flex-wrap items-center gap-1">
            {toggleBtn('zones', 'Bölgeler', 'linear-gradient(180deg,#17C987 50%,#F04B5B 50%)')}
            {toggleBtn('levels', 'Seviyeler', COLORS.fg)}
            {!overlay.preview && toggleBtn('projection', 'Projeksiyon', COLORS.proj, !hasProjection)}
            {!overlay.preview && toggleBtn('markers', 'İşaretler', COLORS.accent)}
          </div>
        )}
      </div>
      <div className="relative" style={{ height }}>
        <div ref={containerRef} className="absolute inset-0" />
        {/* Gösterge */}
        <div className="pointer-events-none absolute left-3 top-2.5 z-10 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11.5px]">
          <span className="font-semibold text-fg">{symbol}</span>
          <span className="text-subtle">{interval}</span>
          {shown && (
            <span className="num flex flex-wrap gap-x-2.5 text-muted">
              <span>
                A <span className="text-fg">{fmtPrice(shown.open)}</span>
              </span>
              <span>
                Y <span className="text-fg">{fmtPrice(shown.high)}</span>
              </span>
              <span>
                D <span className="text-fg">{fmtPrice(shown.low)}</span>
              </span>
              <span>
                K <span className={shown.close >= shown.open ? 'text-up' : 'text-down'}>{fmtPrice(shown.close)}</span>
              </span>
              {chg !== null && <span className={chg >= 0 ? 'text-up' : 'text-down'}>{fmtPct(chg, { sign: true })}</span>}
              {shown.volume > 0 && <span>Hcm {fmtCompact(shown.volume)}</span>}
              {!hovering && <span className="text-subtle">· canlı</span>}
            </span>
          )}
        </div>
        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-surface/60">
            <Loader2 className="h-5 w-5 animate-spin text-muted" />
          </div>
        )}
        {error && !loading && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-surface/80 text-[12.5px] text-muted">
            <AlertTriangle className="h-5 w-5 text-warn" />
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
