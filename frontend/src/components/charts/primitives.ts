// lightweight-charts v4 özel çizim katmanları (series primitives):
//  - ZonesPrimitive: giriş ↔ stop (kırmızı) ve giriş ↔ hedef (yeşil) bölgeleri
//  - ConePrimitive: volatilite konisi dolguları (1σ / 2σ) ve hedef işareti
import type {
  AutoscaleInfo,
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  ISeriesPrimitivePaneRenderer,
  ISeriesPrimitivePaneView,
  SeriesAttachedParameter,
  SeriesType,
  Time,
  UTCTimestamp,
} from 'lightweight-charts';
import type { CanvasRenderingTarget2D } from 'fancy-canvas';

abstract class BasePrimitive implements ISeriesPrimitive<Time> {
  chart: IChartApi | null = null;
  series: ISeriesApi<SeriesType> | null = null;
  private requestUpdate: (() => void) | null = null;

  attached(p: SeriesAttachedParameter<Time>) {
    this.chart = p.chart as unknown as IChartApi;
    this.series = p.series as unknown as ISeriesApi<SeriesType>;
    this.requestUpdate = p.requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  protected redraw() {
    this.requestUpdate?.();
  }
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function pill(
  ctx: CanvasRenderingContext2D,
  text: string,
  xIn: number,
  y: number,
  pr: number,
  fg: string,
  bg: string,
  border: string,
  anchor: 'left' | 'right' = 'left',
) {
  ctx.font = `600 ${11 * pr}px "Inter Variable", Inter, system-ui, sans-serif`;
  const padX = 6 * pr;
  const h = 18 * pr;
  const w = ctx.measureText(text).width + padX * 2;
  // anchor=right: x, etiketin sağ kenarıdır
  const x = anchor === 'right' ? xIn - w : xIn;
  roundRect(ctx, x, y - h / 2, w, h, 4 * pr);
  ctx.fillStyle = bg;
  ctx.fill();
  ctx.lineWidth = 1 * pr;
  ctx.strokeStyle = border;
  ctx.stroke();
  ctx.fillStyle = fg;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, x + padX, y + 0.5 * pr);
}

/* ------------------------------------------------------------------ */
/* Bölgeler                                                            */
/* ------------------------------------------------------------------ */

export interface ZonesData {
  side: 'LONG' | 'SHORT';
  entry: number;
  stop: number;
  target: number | null;
  /** Bölgenin başladığı grafik zamanı (kaydırılmış saniye). null → sol kenar. */
  startTime: number | null;
  /** Bölgenin bittiği grafik zamanı. null → sağ kenar. */
  endTime: number | null;
  riskLabel: string;
  rewardLabel: string;
  dim?: boolean;
}

class ZonesRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(
    private readonly c: {
      x1: number | null;
      x2: number | null;
      yEntry: number | null;
      yStop: number | null;
      yTarget: number | null;
      riskLabel: string;
      rewardLabel: string;
      dim: boolean;
    },
  ) {}

  draw(target: CanvasRenderingTarget2D) {
    const c = this.c;
    if (c.yEntry === null) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
      const x1 = Math.max(0, Math.round((c.x1 ?? 0) * hr));
      const x2 = c.x2 === null ? bitmapSize.width : Math.min(bitmapSize.width, Math.round(c.x2 * hr));
      if (x2 <= x1) return;
      const yE = Math.round((c.yEntry as number) * vr);
      const a = c.dim ? 0.55 : 1;

      // Kâr bölgesi
      if (c.yTarget !== null) {
        const yT = Math.round(c.yTarget * vr);
        const top = Math.min(yE, yT);
        const h = Math.abs(yT - yE);
        const grad = ctx.createLinearGradient(0, yT, 0, yE);
        grad.addColorStop(0, `rgba(23,201,135,${0.2 * a})`);
        grad.addColorStop(1, `rgba(23,201,135,${0.06 * a})`);
        ctx.fillStyle = grad;
        ctx.fillRect(x1, top, x2 - x1, h);
        ctx.fillStyle = `rgba(23,201,135,${0.55 * a})`;
        ctx.fillRect(x1, yT, x2 - x1, Math.max(1, Math.round(hr)));
        if (h > 24 * vr && c.rewardLabel) {
          pill(ctx, c.rewardLabel, x1 + 8 * hr, yT + (yT < yE ? 14 : -14) * vr, hr, '#17C987', 'rgba(10,13,18,0.85)', 'rgba(23,201,135,0.45)');
        }
      }
      // Risk bölgesi
      if (c.yStop !== null) {
        const yS = Math.round(c.yStop * vr);
        const top = Math.min(yE, yS);
        const h = Math.abs(yS - yE);
        const grad = ctx.createLinearGradient(0, yE, 0, yS);
        grad.addColorStop(0, `rgba(240,75,91,${0.07 * a})`);
        grad.addColorStop(1, `rgba(240,75,91,${0.22 * a})`);
        ctx.fillStyle = grad;
        ctx.fillRect(x1, top, x2 - x1, h);
        ctx.fillStyle = `rgba(240,75,91,${0.6 * a})`;
        ctx.fillRect(x1, yS, x2 - x1, Math.max(1, Math.round(hr)));
        if (h > 24 * vr && c.riskLabel) {
          pill(ctx, c.riskLabel, x1 + 8 * hr, yS + (yS > yE ? -14 : 14) * vr, hr, '#F04B5B', 'rgba(10,13,18,0.85)', 'rgba(240,75,91,0.45)');
        }
      }
      // Giriş dikey çizgisi
      if (c.x1 !== null && c.x1 > 0) {
        ctx.save();
        ctx.setLineDash([3 * hr, 3 * hr]);
        ctx.strokeStyle = 'rgba(231,236,243,0.35)';
        ctx.lineWidth = 1 * hr;
        const ys = [yE, c.yStop !== null ? c.yStop * vr : yE, c.yTarget !== null ? c.yTarget * vr : yE];
        ctx.beginPath();
        ctx.moveTo(x1 + 0.5, Math.min(...ys));
        ctx.lineTo(x1 + 0.5, Math.max(...ys));
        ctx.stroke();
        ctx.restore();
      }
    });
  }
}

class ZonesView implements ISeriesPrimitivePaneView {
  constructor(private readonly src: ZonesPrimitive) {}
  zOrder() {
    return 'bottom' as const;
  }
  renderer(): ISeriesPrimitivePaneRenderer | null {
    const d = this.src.data;
    const s = this.src.series;
    const ch = this.src.chart;
    if (!d || !s || !ch) return null;
    const ts = ch.timeScale();
    const x1 = d.startTime === null ? null : ts.timeToCoordinate(d.startTime as UTCTimestamp);
    let x2: number | null = null;
    if (d.endTime !== null) x2 = ts.timeToCoordinate(d.endTime as UTCTimestamp);
    return new ZonesRenderer({
      x1: x1 === null ? (d.startTime === null ? null : this.src.fallbackX(d.startTime)) : x1,
      x2,
      yEntry: s.priceToCoordinate(d.entry),
      yStop: s.priceToCoordinate(d.stop),
      yTarget: d.target === null ? null : s.priceToCoordinate(d.target),
      riskLabel: d.riskLabel,
      rewardLabel: d.rewardLabel,
      dim: !!d.dim,
    });
  }
}

export class ZonesPrimitive extends BasePrimitive {
  data: ZonesData | null = null;
  /** Zaman ölçeğinde olmayan bir zaman için koordinat tahmini (ör. veri öncesi). */
  fallbackX: (t: number) => number | null = () => 0;
  private readonly views = [new ZonesView(this)];

  setData(d: ZonesData | null) {
    this.data = d;
    this.redraw();
  }

  paneViews() {
    return this.views;
  }

  autoscaleInfo(): AutoscaleInfo | null {
    const d = this.data;
    if (!d) return null;
    const vals = [d.entry, d.stop, d.target].filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
    if (!vals.length) return null;
    return { priceRange: { minValue: Math.min(...vals), maxValue: Math.max(...vals) } };
  }
}

/* ------------------------------------------------------------------ */
/* Volatilite konisi                                                   */
/* ------------------------------------------------------------------ */

export interface ConeDrawPoint {
  time: number; // grafik zamanı (kaydırılmış sn)
  up1: number;
  dn1: number;
  up2: number;
  dn2: number;
}

export interface ConeData {
  points: ConeDrawPoint[];
  target: { time: number; price: number; label: string } | null;
}

class ConeRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(
    private readonly pts: { x: number; u1: number; d1: number; u2: number; d2: number }[],
    private readonly target: { x: number; y: number; label: string } | null,
  ) {}

  draw(target: CanvasRenderingTarget2D) {
    const pts = this.pts;
    const tgt = this.target;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      if (pts.length >= 2) {
        const band = (key1: 'u1' | 'u2', key2: 'd1' | 'd2', fill: string | CanvasGradient) => {
          ctx.beginPath();
          pts.forEach((p, i) => {
            const x = p.x * hr;
            const y = p[key1] * vr;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          });
          for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(pts[i].x * hr, pts[i][key2] * vr);
          ctx.closePath();
          ctx.fillStyle = fill;
          ctx.fill();
        };
        const x0 = pts[0].x * hr;
        const xn = pts[pts.length - 1].x * hr;
        const g2 = ctx.createLinearGradient(x0, 0, xn, 0);
        g2.addColorStop(0, 'rgba(167,139,250,0.10)');
        g2.addColorStop(1, 'rgba(167,139,250,0.04)');
        band('u2', 'd2', g2);
        const g1 = ctx.createLinearGradient(x0, 0, xn, 0);
        g1.addColorStop(0, 'rgba(167,139,250,0.20)');
        g1.addColorStop(1, 'rgba(167,139,250,0.08)');
        band('u1', 'd1', g1);
      }
      if (tgt) {
        const x = tgt.x * hr;
        const y = tgt.y * vr;
        const r = 5 * hr;
        ctx.beginPath();
        ctx.moveTo(x, y - r);
        ctx.lineTo(x + r, y);
        ctx.lineTo(x, y + r);
        ctx.lineTo(x - r, y);
        ctx.closePath();
        ctx.fillStyle = '#A78BFA';
        ctx.fill();
        ctx.lineWidth = 2 * hr;
        ctx.strokeStyle = '#0A0D12';
        ctx.stroke();
        // Etiket elmasın soluna çizilir; sağdaki fiyat çizgisi başlıklarıyla çakışmasın
        if (tgt.label) pill(ctx, tgt.label, x - 9 * hr, y - 16 * vr, hr, '#C4B5FD', 'rgba(10,13,18,0.9)', 'rgba(167,139,250,0.55)', 'right');
      }
    });
  }
}

class ConeView implements ISeriesPrimitivePaneView {
  constructor(private readonly src: ConePrimitive) {}
  zOrder() {
    return 'bottom' as const;
  }
  renderer(): ISeriesPrimitivePaneRenderer | null {
    const d = this.src.data;
    const s = this.src.series;
    const ch = this.src.chart;
    if (!d || !s || !ch) return null;
    const ts = ch.timeScale();
    const pts: { x: number; u1: number; d1: number; u2: number; d2: number }[] = [];
    for (const p of d.points) {
      const x = ts.timeToCoordinate(p.time as UTCTimestamp);
      const u1 = s.priceToCoordinate(p.up1);
      const d1 = s.priceToCoordinate(p.dn1);
      const u2 = s.priceToCoordinate(p.up2);
      const d2 = s.priceToCoordinate(p.dn2);
      if (x === null || u1 === null || d1 === null || u2 === null || d2 === null) continue;
      pts.push({ x, u1, d1, u2, d2 });
    }
    let tgt: { x: number; y: number; label: string } | null = null;
    if (d.target) {
      const x = ts.timeToCoordinate(d.target.time as UTCTimestamp);
      const y = s.priceToCoordinate(d.target.price);
      if (x !== null && y !== null) tgt = { x, y, label: d.target.label };
    }
    return new ConeRenderer(pts, tgt);
  }
}

export class ConePrimitive extends BasePrimitive {
  data: ConeData | null = null;
  private readonly views = [new ConeView(this)];

  setData(d: ConeData | null) {
    this.data = d;
    this.redraw();
  }

  paneViews() {
    return this.views;
  }
}
