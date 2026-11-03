import clsx from 'clsx';
import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Gauge, Layers, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { api } from '../../api/client';
import type { Regime, SymbolAnalysis, TfAnalysis, TrendDir } from '../../api/types';
import { useAsync, useInterval, useNow } from '../../lib/hooks';
import { fmtCompact, fmtCountdown, fmtNum, fmtPct, fmtPrice, fmtTimeSec, pnlClass, tfLabel } from '../../lib/format';
import { useSettingsCache } from '../trade/useRiskBounds';
import { InfoTip, Tooltip } from '../ui/overlay';
import { Badge, Skeleton } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
/** Yüzde birimli değer (1.25 = %1,25) → oran biçimi. */
const pctU = (v: number | null | undefined, sign = true, digits = 2) => (isNum(v) ? fmtPct(v / 100, { sign, digits }) : '—');

const TREND_META: Record<TrendDir, { label: string; tone: 'up' | 'down' | 'neutral'; icon: ReactNode }> = {
  up: { label: 'Yükseliş', tone: 'up', icon: <ArrowUpRight className="h-3.5 w-3.5" /> },
  down: { label: 'Düşüş', tone: 'down', icon: <ArrowDownRight className="h-3.5 w-3.5" /> },
  flat: { label: 'Yatay', tone: 'neutral', icon: <ArrowRight className="h-3.5 w-3.5" /> },
};

export function TrendChip({ trend, compact }: { trend: TrendDir; compact?: boolean }) {
  const m = TREND_META[trend] ?? TREND_META.flat;
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-md border font-semibold',
        compact ? 'h-[20px] px-1.5 text-[10.5px]' : 'h-[22px] px-2 text-[11px]',
        m.tone === 'up' && 'border-up/25 bg-up/10 text-up',
        m.tone === 'down' && 'border-down/25 bg-down/10 text-down',
        m.tone === 'neutral' && 'border-line bg-elevated text-muted',
      )}
    >
      {m.icon}
      {!compact && m.label}
    </span>
  );
}

export function RegimeChip({ regime }: { regime: Regime }) {
  const tone =
    regime.name === 'BULL' ? 'up' : regime.name === 'BEAR' ? 'down' : regime.name === 'CHAOS' ? 'warn' : regime.name === 'RANGE' ? 'accent' : 'neutral';
  return (
    <Badge tone={tone} dot>
      {regime.label || regime.name}
    </Badge>
  );
}

function RsiCell({ v }: { v: number | null }) {
  if (!isNum(v)) return <span className="text-subtle">—</span>;
  const cls = v >= 70 ? 'text-warn' : v <= 30 ? 'text-accent' : 'text-fg';
  const hint = v >= 70 ? 'Aşırı alım bölgesi' : v <= 30 ? 'Aşırı satım bölgesi' : 'Nötr bölge';
  return (
    <Tooltip content={`RSI ${fmtNum(v, 1)} — ${hint}`}>
      <span className="flex items-center justify-end gap-2" tabIndex={0}>
        <span className="relative hidden h-1.5 w-12 rounded-full bg-elevated sm:block">
          <span className="absolute inset-y-0 left-[30%] right-[30%] bg-line-strong/50" />
          <span
            className={clsx('absolute top-1/2 h-2.5 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-sm', v >= 70 ? 'bg-warn' : v <= 30 ? 'bg-accent' : 'bg-fg/80')}
            style={{ left: `${Math.max(0, Math.min(100, v))}%` }}
          />
        </span>
        <span className={clsx('num', cls)}>{fmtNum(v, 0)}</span>
      </span>
    </Tooltip>
  );
}

function confluenceTone(c: SymbolAnalysis['confluence']): 'up' | 'down' | 'neutral' {
  if (c.long >= 2 && c.long > c.short) return 'up';
  if (c.short >= 2 && c.short > c.long) return 'down';
  return 'neutral';
}

function confluenceText(c: SymbolAnalysis['confluence'], n: number): string {
  if (c.label) return c.label;
  if (c.long >= 2 && c.long > c.short) return `${c.long}/${n} zaman dilimi yükselişte`;
  if (c.short >= 2 && c.short > c.long) return `${c.short}/${n} zaman dilimi düşüşte`;
  return 'Zaman dilimleri karışık';
}

function useAnalysis(symbol: string | null | undefined) {
  const st = useAsync<SymbolAnalysis | null>(() => (symbol ? api.analysis(symbol) : Promise.resolve(null)), [symbol]);
  useInterval(() => {
    if (symbol) void st.reload();
  }, symbol ? 15_000 : null);
  return st;
}

function MtfTable({ rows }: { rows: TfAnalysis[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] whitespace-nowrap text-[12.5px]">
        <thead className="table-head border-y border-line">
          <tr>
            <th>Zaman dilimi</th>
            <th>Trend</th>
            <th className="!text-right">RSI</th>
            <th className="!text-right">
              <span className="inline-flex items-center gap-1">
                ADX <InfoTip>Trend gücü. 25 üstü güçlü, 20 altı zayıf/yatay.</InfoTip>
              </span>
            </th>
            <th className="!text-right">ATR %</th>
            <th className="!text-right">
              <span className="inline-flex items-center gap-1">
                %B <InfoTip>Bollinger %B: 0 alt bant, 1 üst bant. 1 üstü bandın dışında (aşırı uzama).</InfoTip>
              </span>
            </th>
            <th className="!text-right">
              <span className="inline-flex items-center gap-1">
                EMA20 mesafe <InfoTip>Fiyatın EMA20'ye uzaklığı, ATR cinsinden. ±3 üstü aşırı uzamış sayılır.</InfoTip>
              </span>
            </th>
            <th className="!text-right">
              <span className="inline-flex items-center gap-1">
                Hacim z <InfoTip>Son barın hacminin (log) ortalamaya göre sapması. 2 üstü belirgin hacim artışı.</InfoTip>
              </span>
            </th>
            <th className="!text-right">24 bar</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.tf} className="table-row border-b border-line/60">
              <td className="font-medium text-fg">{tfLabel(r.tf)}</td>
              <td>
                <Tooltip content={r.ema_stack || 'EMA dizilimi bilinmiyor'}>
                  <span tabIndex={0}>
                    <TrendChip trend={r.trend} />
                  </span>
                </Tooltip>
              </td>
              <td className="text-right">
                <RsiCell v={r.rsi} />
              </td>
              <td className={clsx('num text-right', isNum(r.adx) && r.adx >= 25 ? 'font-semibold text-fg' : 'text-muted')}>{isNum(r.adx) ? fmtNum(r.adx, 0) : '—'}</td>
              <td className="num text-right text-muted">{pctU(r.atr_pct, false)}</td>
              <td className={clsx('num text-right', isNum(r.bb_pctb) && (r.bb_pctb > 1 ? 'text-warn' : r.bb_pctb < 0 ? 'text-accent' : 'text-muted'))}>
                {isNum(r.bb_pctb) ? fmtNum(r.bb_pctb, 2) : '—'}
              </td>
              <td className={clsx('num text-right', isNum(r.dist_ema20_atr) && Math.abs(r.dist_ema20_atr) >= 3 ? 'text-warn' : 'text-muted')}>
                {isNum(r.dist_ema20_atr) ? `${r.dist_ema20_atr > 0 ? '+' : ''}${fmtNum(r.dist_ema20_atr, 1)}` : '—'}
              </td>
              <td className={clsx('num text-right', isNum(r.vol_z) && r.vol_z >= 2 ? 'font-semibold text-accent' : 'text-muted')}>{isNum(r.vol_z) ? fmtNum(r.vol_z, 1) : '—'}</td>
              <td className={clsx('num text-right', pnlClass(r.change_pct))}>{pctU(r.change_pct)}</td>
            </tr>
          ))}
          {!rows.length && (
            <tr>
              <td colSpan={9} className="px-3 py-4 text-center text-[12px] text-subtle">
                Zaman dilimi verisi yok
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Kv({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="kv">
      <dt className="flex items-center gap-1">
        {label}
        {hint && <InfoTip>{hint}</InfoTip>}
      </dt>
      <dd>{children}</dd>
    </div>
  );
}

function fundingText(fr: number | null): ReactNode {
  if (!isNum(fr)) return '—';
  return (
    <span className={clsx('whitespace-nowrap', pnlClass(fr))}>
      {fmtPct(fr, { sign: true, digits: 4 })}
      <span className="ml-1 hidden text-[11px] text-subtle 2xl:inline">{fr > 0 ? "long'lar öder" : fr < 0 ? "short'lar öder" : ''}</span>
    </span>
  );
}

/**
 * Bir sembol için çok zaman dilimli teknik + türev + likidite analizi.
 * `compact` sürümü dar alanlar (manuel emir bileti) içindir.
 */
export function SymbolAnalysisPanel({ symbol, compact, className }: { symbol: string | null | undefined; compact?: boolean; className?: string }) {
  const st = useAnalysis(symbol);
  const now = useNow(1000);
  const settings = useSettingsCache();
  const maxSpread = settings?.scanner.max_spread_bps ?? null;
  const a = st.data;

  if (!symbol) return null;

  const header = (
    <div className="card-head">
      <div className="flex min-w-0 items-center gap-2.5">
        <Activity className="h-4 w-4 shrink-0 text-subtle" />
        <div className="min-w-0">
          <div className="card-title flex items-center gap-1.5">
            Piyasa analizi
            <InfoTip>
              15 dk, 1 sa ve 4 sa zaman dilimlerinde trend, momentum ve oynaklık; türev piyasası (funding, open interest, likidasyonlar) ve likidite.
              Karar desteği içindir, garanti değildir.
            </InfoTip>
          </div>
          <div className="truncate text-[11.5px] text-subtle">
            {symbol}
            {a ? ` · ${fmtTimeSec(a.updated_at)} güncellendi` : ''}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {a && <RegimeChip regime={a.regime} />}
        {a && (
          <Badge tone={confluenceTone(a.confluence)}>
            <Layers className="h-3 w-3" />
            {confluenceText(a.confluence, a.timeframes.length || 3)}
          </Badge>
        )}
        <button
          type="button"
          onClick={() => void st.reload()}
          className="rounded-md p-1 text-subtle transition-colors hover:bg-hover hover:text-fg"
          aria-label="Analizi yenile"
          title="Yenile"
        >
          <RefreshCw className={clsx('h-3.5 w-3.5', st.loading && 'animate-spin')} />
        </button>
      </div>
    </div>
  );

  if (!a) {
    return (
      <div className={clsx('card', className)}>
        {header}
        <div className="px-4 pb-4">
          {st.loading ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-3/4" />
            </div>
          ) : (
            <div className="rounded-lg border border-line bg-bg/50 px-3 py-3 text-[12px] text-muted">Analiz alınamadı{st.error ? `: ${st.error}` : ''}</div>
          )}
        </div>
      </div>
    );
  }

  const d = a.derivatives;
  const m = a.market;
  const lq = a.liquidity;
  const spreadBad = isNum(lq.spread_bps) && isNum(maxSpread) && lq.spread_bps > maxSpread;
  const fundingLeft = isNum(d.next_funding_time) ? d.next_funding_time - now : null;

  if (compact) {
    const tf1h = a.timeframes.find((t) => t.tf === '1h') ?? a.timeframes[0];
    return (
      <div className={clsx('card', className)}>
        {header}
        <div className="space-y-3 px-4 pb-4">
          <div className="grid grid-cols-3 gap-2">
            {a.timeframes.map((t) => (
              <div key={t.tf} className="rounded-lg border border-line bg-bg/50 px-2.5 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-muted">{tfLabel(t.tf)}</span>
                  <TrendChip trend={t.trend} compact />
                </div>
                <div className="num mt-1 flex items-center justify-between text-[11px] text-subtle">
                  <span>RSI {isNum(t.rsi) ? fmtNum(t.rsi, 0) : '—'}</span>
                  <span>ADX {isNum(t.adx) ? fmtNum(t.adx, 0) : '—'}</span>
                </div>
              </div>
            ))}
          </div>
          <dl className="grid grid-cols-2 gap-x-5 divide-line">
            <Kv label="Funding">{fundingText(d.funding_rate)}</Kv>
            <Kv label="OI 1 sa">
              <span className={pnlClass(d.oi_change_1h_pct)}>{pctU(d.oi_change_1h_pct)}</span>
            </Kv>
            <Kv label="Spread">
              <span className={spreadBad ? 'text-warn' : 'text-fg'}>{isNum(lq.spread_bps) ? `${fmtNum(lq.spread_bps, 1)} bps` : '—'}</span>
            </Kv>
            <Kv label="BTC korelasyon">{isNum(m.btc_corr) ? fmtNum(m.btc_corr, 2) : '—'}</Kv>
            <Kv label="ATR (1 sa)">{pctU(tf1h?.atr_pct, false)}</Kv>
            <Kv label="24 sa">
              <span className={pnlClass(m.change_24h_pct)}>{pctU(m.change_24h_pct)}</span>
            </Kv>
          </dl>
          {a.notes.length > 0 && (
            <ul className="space-y-1 border-t border-line pt-2.5 text-[11.5px] text-muted">
              {a.notes.slice(0, 3).map((n) => (
                <li key={n} className="flex gap-1.5">
                  <span className="text-subtle">•</span>
                  {n}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={clsx('card overflow-hidden', className)}>
      {header}
      <MtfTable rows={a.timeframes} />
      <div className="grid gap-x-6 gap-y-1 px-4 pb-3 pt-2 md:grid-cols-3">
        <div>
          <div className="label mb-1 mt-2 flex items-center gap-1.5">
            <Gauge className="h-3 w-3" /> Türev piyasası
          </div>
          <dl className="divide-y divide-line">
            <Kv label="Funding" hint="8 saatlik funding oranı. Pozitifse long pozisyonlar short'lara ödeme yapar (kalabalık long işareti).">
              {fundingText(d.funding_rate)}
            </Kv>
            <Kv label="Sonraki funding">{fundingLeft !== null ? fmtCountdown(fundingLeft) : '—'}</Kv>
            <Kv label="Open interest 1 sa" hint="Açık pozisyon miktarının son 1 saatteki değişimi. Fiyatla birlikte artışı yeni paranın girdiğini gösterir.">
              <span className={pnlClass(d.oi_change_1h_pct)}>{pctU(d.oi_change_1h_pct)}</span>
            </Kv>
            <Kv label="Likidasyon 1 sa" hint="Son 1 saatte zorla kapatılan long ve short pozisyonların büyüklüğü (USDT).">
              {isNum(d.liq_long_1h) || isNum(d.liq_short_1h) ? (
                <span>
                  <span className="text-down">L {fmtCompact(d.liq_long_1h)}</span>
                  <span className="text-subtle"> · </span>
                  <span className="text-up">S {fmtCompact(d.liq_short_1h)}</span>
                </span>
              ) : (
                '—'
              )}
            </Kv>
          </dl>
        </div>
        <div>
          <div className="label mb-1 mt-2">Piyasa</div>
          <dl className="divide-y divide-line">
            <Kv label="24 sa değişim">
              <span className={pnlClass(m.change_24h_pct)}>{pctU(m.change_24h_pct)}</span>
            </Kv>
            <Kv label="24 sa hacim">
              {fmtCompact(m.quote_volume_24h)}
              {isNum(m.volume_rank) && <span className="ml-1 text-[11px] text-subtle">#{m.volume_rank}</span>}
            </Kv>
            <Kv label="BTC korelasyon / beta" hint="1 saatlik getirilerle BTC'ye korelasyon (−1..1) ve beta (BTC %1 hareket ederse beklenen hareket).">
              {isNum(m.btc_corr) ? fmtNum(m.btc_corr, 2) : '—'} <span className="text-subtle">/</span> {isNum(m.btc_beta) ? fmtNum(m.btc_beta, 2) : '—'}
            </Kv>
            <Kv label="BTC'ye göre güç 24 sa" hint="Sembolün son 24 saatlik getirisinin BTC'ninkinden farkı.">
              <span className={pnlClass(m.rs_24h_pct)}>{pctU(m.rs_24h_pct)}</span>
            </Kv>
          </dl>
        </div>
        <div>
          <div className="label mb-1 mt-2">Likidite</div>
          <dl className="divide-y divide-line">
            <Kv label="Alış / satış">
              {fmtPrice(lq.bid)} <span className="text-subtle">/</span> {fmtPrice(lq.ask)}
            </Kv>
            <Kv
              label="Spread"
              hint={isNum(maxSpread) ? `Ayarlardaki üst sınır ${fmtNum(maxSpread, 1)} bps. Üstündeyse işleme girilmez.` : 'Alış-satış farkı (baz puan).'}
            >
              <span className={clsx(isNum(lq.spread_bps) ? (spreadBad ? 'text-warn' : 'text-up') : '')}>
                {isNum(lq.spread_bps) ? `${fmtNum(lq.spread_bps, 1)} bps` : '—'}
              </span>
              {spreadBad && <span className="ml-1 text-[11px] text-warn">sınır üstü</span>}
            </Kv>
            <Kv label="Fiyat">{fmtPrice(a.price)}</Kv>
          </dl>
        </div>
      </div>
      {a.notes.length > 0 && (
        <ul className="grid gap-1.5 border-t border-line bg-bg/30 px-4 py-3 text-[12px] text-muted md:grid-cols-2">
          {a.notes.map((n) => (
            <li key={n} className="flex gap-2">
              <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent" />
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
