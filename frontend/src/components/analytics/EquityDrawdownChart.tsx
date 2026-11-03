import { ColorType, LineStyle, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { useEffect, useRef } from 'react';
import { fmtNum, fmtUsd } from '../../lib/format';

const TZ_SHIFT = -new Date().getTimezoneOffset() * 60;

export interface EquityDdPoint {
  /** saniye */
  time: number;
  equity: number;
  /** yüzde; işareti ne olursa olsun düşüş olarak (negatif) çizilir */
  drawdown_pct: number;
}

/**
 * Üstte özsermaye eğrisi (başlangıca göre yeşil/kırmızı), altta "su altı" düşüş grafiği (%).
 * lightweight-charts v4'te ayrı pane olmadığı için düşüş, alt kısma yerleştirilmiş ayrı bir fiyat
 * ölçeğinde çizilir.
 */
export function EquityDrawdownChart({
  data,
  initial,
  height = 340,
}: {
  data: EquityDdPoint[];
  initial?: number | null;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const eqRef = useRef<ISeriesApi<'Baseline'> | null>(null);
  const ddRef = useRef<ISeriesApi<'Baseline'> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8B95A5',
        fontSize: 11,
        fontFamily: '"Inter Variable", Inter, sans-serif',
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(34,41,54,0.45)' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.06, bottom: 0.34 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: {
        vertLine: { color: '#5D6676', style: LineStyle.Dashed, labelBackgroundColor: '#1B212C' },
        horzLine: { color: '#5D6676', style: LineStyle.Dashed, labelBackgroundColor: '#1B212C' },
      },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: false, axisDoubleClickReset: true },
      localization: { locale: 'tr-TR', priceFormatter: (p: number) => fmtUsd(p) },
    });
    const eq = chart.addBaselineSeries({
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#17C987',
      topFillColor1: 'rgba(23,201,135,0.22)',
      topFillColor2: 'rgba(23,201,135,0.02)',
      bottomLineColor: '#F04B5B',
      bottomFillColor1: 'rgba(240,75,91,0.02)',
      bottomFillColor2: 'rgba(240,75,91,0.22)',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      title: 'Özsermaye',
    });
    const dd = chart.addBaselineSeries({
      priceScaleId: 'dd',
      baseValue: { type: 'price', price: 0 },
      topLineColor: 'rgba(0,0,0,0)',
      topFillColor1: 'rgba(0,0,0,0)',
      topFillColor2: 'rgba(0,0,0,0)',
      bottomLineColor: '#F04B5B',
      bottomFillColor1: 'rgba(240,75,91,0.08)',
      bottomFillColor2: 'rgba(240,75,91,0.38)',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'custom', minMove: 0.01, formatter: (v: number) => `%${fmtNum(v, 1)}` },
      title: 'Düşüş',
    });
    chart.priceScale('dd').applyOptions({ scaleMargins: { top: 0.72, bottom: 0.02 }, borderVisible: false });
    chartRef.current = chart;
    eqRef.current = eq;
    ddRef.current = dd;
    return () => {
      chart.remove();
      chartRef.current = null;
      eqRef.current = null;
      ddRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const eq = eqRef.current;
    const dd = ddRef.current;
    if (!chart || !eq || !dd) return;
    const seen = new Set<number>();
    const pts = [...data]
      .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.equity))
      .sort((a, b) => a.time - b.time)
      .filter((p) => {
        if (seen.has(p.time)) return false;
        seen.add(p.time);
        return true;
      });
    const base = initial ?? (pts.length ? pts[0].equity : 0);
    eq.applyOptions({ baseValue: { type: 'price', price: base } });
    eq.setData(pts.map((p) => ({ time: (p.time + TZ_SHIFT) as UTCTimestamp, value: p.equity })));
    dd.setData(
      pts.map((p) => ({
        time: (p.time + TZ_SHIFT) as UTCTimestamp,
        value: Number.isFinite(p.drawdown_pct) ? -Math.abs(p.drawdown_pct) : 0,
      })),
    );
    chart.timeScale().fitContent();
  }, [data, initial]);

  return (
    <div className="relative">
      <div className="pointer-events-none absolute left-3 top-2 z-10 flex items-center gap-4 text-[11px] text-muted">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-3.5 rounded bg-up" /> Özsermaye
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-3.5 rounded-sm bg-down/40 ring-1 ring-down/60" /> Düşüş (%)
        </span>
      </div>
      <div ref={ref} style={{ height }} className="w-full" />
    </div>
  );
}
