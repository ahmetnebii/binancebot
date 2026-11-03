import { ColorType, LineStyle, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { useEffect, useRef } from 'react';
import type { EquityPoint } from '../../api/types';
import { fmtUsd } from '../../lib/format';

const TZ_SHIFT = -new Date().getTimezoneOffset() * 60;

export function EquityChart({ data, height = 240, baseline }: { data: EquityPoint[]; height?: number; baseline?: number | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Baseline'> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8B95A5', fontSize: 11, fontFamily: '"Inter Variable", Inter, sans-serif', attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(34,41,54,0.5)' } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.08 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: {
        vertLine: { color: '#5D6676', style: LineStyle.Dashed, labelBackgroundColor: '#1B212C' },
        horzLine: { color: '#5D6676', style: LineStyle.Dashed, labelBackgroundColor: '#1B212C' },
      },
      handleScroll: false,
      handleScale: false,
      localization: { locale: 'tr-TR', priceFormatter: (p: number) => fmtUsd(p) },
    });
    const s = chart.addBaselineSeries({
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#17C987',
      topFillColor1: 'rgba(23,201,135,0.22)',
      topFillColor2: 'rgba(23,201,135,0.02)',
      bottomLineColor: '#F04B5B',
      bottomFillColor1: 'rgba(240,75,91,0.02)',
      bottomFillColor2: 'rgba(240,75,91,0.22)',
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });
    chartRef.current = chart;
    seriesRef.current = s;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const s = seriesRef.current;
    const chart = chartRef.current;
    if (!s || !chart) return;
    const seen = new Set<number>();
    const pts = [...data]
      .sort((a, b) => a.time - b.time)
      .filter((p) => {
        if (seen.has(p.time)) return false;
        seen.add(p.time);
        return Number.isFinite(p.equity);
      })
      .map((p) => ({ time: (p.time + TZ_SHIFT) as UTCTimestamp, value: p.equity }));
    const base = baseline ?? (pts.length ? pts[0].value : 0);
    s.applyOptions({ baseValue: { type: 'price', price: base } });
    s.setData(pts);
    chart.timeScale().fitContent();
  }, [data, baseline]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
