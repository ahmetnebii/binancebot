import clsx from 'clsx';
import { Activity, ArrowRight, Gauge, Layers, Radar, ShieldCheck, ShieldAlert, Target, TrendingDown, TrendingUp, Wallet } from 'lucide-react';
import type { ReactNode } from 'react';
import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type { EquityPoint, Regime, Signal } from '../api/types';
import { EquityChart } from '../components/charts/EquityChart';
import { InfoTip } from '../components/ui/overlay';
import { Badge, Button, Card, CardHeader, Empty, Meter, Segmented, Skeleton } from '../components/ui/primitives';
import { Sparkline } from '../components/ui/viz';
import { PositionCard } from '../components/trade/PositionViews';
import { SignalRow } from '../components/trade/SignalCard';
import { SignalDrawer } from '../components/trade/SignalDrawer';
import { useSettingsCache } from '../components/trade/useRiskBounds';
import { eqDayFrac, eqDrawdownFrac, eqMarginFrac, eqRiskFrac } from '../lib/derive';
import { useAsync, useInterval, useNow } from '../lib/hooks';
import { fmtCountdown, fmtNum, fmtPct, fmtR, fmtRelative, fmtUsd, pnlClass } from '../lib/format';
import { navigate } from '../lib/router';
import { useApp } from '../store/app';

const REGIME_TONE: Record<Regime['name'], 'up' | 'down' | 'accent' | 'warn' | 'neutral'> = {
  BULL: 'up',
  BEAR: 'down',
  RANGE: 'accent',
  CHAOS: 'warn',
  UNKNOWN: 'neutral',
};

function Kpi({
  icon,
  label,
  children,
  footer,
  hint,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
  footer?: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <Card className="flex min-w-0 flex-col p-4">
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-elevated text-muted">{icon}</span>
        <span className="label">{label}</span>
        {hint && <InfoTip className="ml-auto">{hint}</InfoTip>}
      </div>
      <div className="mt-3 min-w-0">{children}</div>
      {footer && <div className="mt-auto pt-3">{footer}</div>}
    </Card>
  );
}

export function Dashboard() {
  const status = useApp((s) => s.status);
  const positions = useApp((s) => s.openPositions);
  const signalsMap = useApp((s) => s.signals);
  const settings = useSettingsCache();
  const [range, setRange] = useState<'1d' | '7d' | '30d' | 'all'>('7d');
  const [openSignal, setOpenSignal] = useState<string | null>(null);
  const now = useNow(1000);

  const equity = useAsync<EquityPoint[]>(() => api.equity(range), [range]);
  const spark = useAsync<EquityPoint[]>(() => api.equity('7d'), []);
  useInterval(() => {
    void equity.reload();
    void spark.reload();
  }, 60_000);

  const activeSignals = useMemo(
    () =>
      Object.values(signalsMap)
        .filter((s) => s.status === 'ACTIVE' && s.expires_at > now)
        .sort((a, b) => b.score - a.score),
    [signalsMap, now],
  );
  const sigObj: Signal | null = openSignal ? signalsMap[openSignal] ?? null : null;

  const eq = status?.equity;
  const stats = status?.stats;
  const heatLimit = settings?.risk.max_portfolio_heat_pct ?? null;
  const marginLimit = settings?.risk.max_total_margin_pct ?? null;
  const regime = status?.regime;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight text-fg">Panel</h1>
          <p className="mt-0.5 text-[12.5px] text-muted">
            {status
              ? `${status.counts.universe} sembol izleniyor · ${status.counts.open_positions} açık pozisyon · ${status.counts.active_signals} aktif fırsat`
              : 'Sistem durumu yükleniyor…'}
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" icon={<Radar className="h-3.5 w-3.5" />} onClick={() => navigate('/firsatlar')}>
            Fırsatlar
          </Button>
          <Button size="sm" variant="primary" icon={<Layers className="h-3.5 w-3.5" />} onClick={() => navigate('/pozisyonlar/yeni')}>
            Yeni işlem
          </Button>
        </div>
      </div>

      {/* KPI */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi icon={<Wallet className="h-3.5 w-3.5" />} label="Özsermaye" hint="Cüzdan bakiyesi + gerçekleşmemiş K/Z. Pozisyon büyüklükleri bu değere oranla hesaplanır.">
          {eq ? (
            <>
              <div className="num text-[22px] font-semibold tracking-tight text-fg">
                {fmtUsd(eq.equity)} <span className="text-[12px] font-medium text-subtle">USDT</span>
              </div>
              <div className="num mt-0.5 text-[11.5px] text-muted">
                Kullanılabilir {fmtUsd(eq.available)} · Zirveden {fmtPct(-eqDrawdownFrac(eq), { sign: true })}
              </div>
            </>
          ) : (
            <Skeleton className="h-8 w-40" />
          )}
          <div className="mt-2">
            <Sparkline values={(spark.data ?? []).map((p) => p.equity)} height={34} />
          </div>
        </Kpi>
        <Kpi icon={eq && eq.day_pnl < 0 ? <TrendingDown className="h-3.5 w-3.5" /> : <TrendingUp className="h-3.5 w-3.5" />} label="Bugünkü K/Z" hint="UTC gün başından bu yana gerçekleşen + gerçekleşmemiş kâr/zarar.">
          {eq ? (
            <>
              <div className={clsx('num text-[22px] font-semibold tracking-tight', pnlClass(eq.day_pnl))}>{fmtUsd(eq.day_pnl, { sign: true })}</div>
              <div className={clsx('num mt-0.5 text-[12px]', pnlClass(eq.day_pnl))}>{fmtPct(eqDayFrac(eq), { sign: true })}</div>
            </>
          ) : (
            <Skeleton className="h-8 w-32" />
          )}
          {eq && (
            <div className="num mt-3 flex justify-between text-[11.5px] text-muted">
              <span>Gerçekleşmemiş</span>
              <span className={pnlClass(eq.unrealized)}>{fmtUsd(eq.unrealized, { sign: true })}</span>
            </div>
          )}
        </Kpi>
        <Kpi
          icon={<ShieldAlert className="h-3.5 w-3.5" />}
          label="Açık risk"
          hint="Tüm stoplar tetiklenirse kaybedilecek toplam tutarın özsermayeye oranı (portföy ısısı). Çizgi: limit."
        >
          {eq ? (
            <>
              <div className="num text-[22px] font-semibold tracking-tight text-fg">{fmtPct(eqRiskFrac(eq))}</div>
              <div className="num mt-0.5 text-[11.5px] text-muted">
                {fmtUsd(eq.open_risk)} USDT{heatLimit ? ` · limit ${fmtPct(heatLimit, { digits: 1 })}` : ''}
              </div>
              <Meter className="mt-3" value={heatLimit ? eqRiskFrac(eq) / (heatLimit * 1.25) : eqRiskFrac(eq) * 10} limit={heatLimit ? 0.8 : null} />
            </>
          ) : (
            <Skeleton className="h-8 w-24" />
          )}
        </Kpi>
        <Kpi icon={<Gauge className="h-3.5 w-3.5" />} label="Marjin kullanımı" hint="Açık pozisyonlara bağlanan teminatın özsermayeye oranı.">
          {eq ? (
            <>
              <div className="num text-[22px] font-semibold tracking-tight text-fg">{fmtPct(eqMarginFrac(eq))}</div>
              <div className="num mt-0.5 text-[11.5px] text-muted">
                {fmtUsd(eq.margin_used)} USDT{marginLimit ? ` · limit ${fmtPct(marginLimit, { digits: 0 })}` : ''}
              </div>
              <Meter className="mt-3" value={eqMarginFrac(eq)} limit={marginLimit} />
            </>
          ) : (
            <Skeleton className="h-8 w-24" />
          )}
        </Kpi>
        <Kpi icon={<Target className="h-3.5 w-3.5" />} label="Performans" hint="Kapanan işlemlere göre kazanma oranı, işlem başına beklenti (R) ve profit factor.">
          {stats && stats.trades === 0 ? (
            <>
              <div className="text-[15px] font-semibold text-fg">Henüz kapanan işlem yok</div>
              <div className="mt-1 text-[11.5px] leading-relaxed text-muted">Kazanma oranı, beklenti (R) ve profit factor ilk işlemler kapandıkça hesaplanır.</div>
            </>
          ) : stats ? (
            <>
              <div className="flex items-baseline gap-2">
                <span className="num text-[22px] font-semibold tracking-tight text-fg">{stats.win_rate != null ? fmtPct(stats.win_rate, { digits: 0 }) : '—'}</span>
                <span className="text-[11.5px] text-muted">kazanma</span>
              </div>
              <div className="num mt-0.5 text-[11.5px] text-muted">
                Beklenti <span className={pnlClass(stats.expectancy_r)}>{fmtR(stats.expectancy_r)}</span> · PF{' '}
                <span className="text-fg">{stats.profit_factor != null ? fmtNum(stats.profit_factor, 2) : '—'}</span>
              </div>
              <div className="num mt-3 flex justify-between text-[11.5px] text-muted">
                <span>{stats.trades} işlem</span>
                <span className={pnlClass(stats.total_pnl)}>{fmtUsd(stats.total_pnl, { sign: true })}</span>
              </div>
            </>
          ) : (
            <Skeleton className="h-8 w-24" />
          )}
        </Kpi>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          {/* Özsermaye eğrisi */}
          <Card>
            <CardHeader
              title="Özsermaye eğrisi"
              subtitle="Başlangıca göre renklendirilmiş (yeşil: kârda, kırmızı: zararda)"
              icon={<Activity className="h-4 w-4" />}
              right={
                <Segmented
                  size="xs"
                  value={range}
                  onChange={setRange}
                  options={[
                    { value: '1d', label: '1G' },
                    { value: '7d', label: '7G' },
                    { value: '30d', label: '30G' },
                    { value: 'all', label: 'Tümü' },
                  ]}
                />
              }
            />
            <div className="px-2 pb-2">
              {equity.data && equity.data.length > 1 ? (
                <EquityChart data={equity.data} height={250} />
              ) : equity.loading ? (
                <Skeleton className="mx-2 mb-2 h-[250px]" />
              ) : (
                <Empty icon={<Activity className="h-5 w-5" />} title="Henüz yeterli veri yok" className="h-[250px]">
                  Sistem çalıştıkça özsermaye her dakika kaydedilir ve burada çizilir.
                </Empty>
              )}
            </div>
          </Card>

          {/* Açık pozisyonlar */}
          <Card>
            <CardHeader
              title="Açık pozisyonlar"
              subtitle={positions.length ? `${positions.length} pozisyon · tıklayarak detay ve grafik` : undefined}
              icon={<Layers className="h-4 w-4" />}
              right={
                <Button size="xs" variant="ghost" onClick={() => navigate('/pozisyonlar')}>
                  Tümü <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              }
            />
            <div className="px-4 pb-4">
              {positions.length ? (
                <div className="grid gap-3 md:grid-cols-2">
                  {positions.map((p) => (
                    <PositionCard key={p.id} p={p} />
                  ))}
                </div>
              ) : (
                <Empty icon={<Layers className="h-5 w-5" />} title="Açık pozisyon yok" action={
                  <Button size="sm" variant="secondary" onClick={() => navigate('/firsatlar')}>
                    Fırsatlara göz at
                  </Button>
                }>
                  {status?.auto_mode
                    ? 'Otomatik mod açık: uygun bir fırsat geldiğinde sistem pozisyon açacak.'
                    : 'Otomatik mod kapalı: fırsatları inceleyip elle işleme girebilirsin.'}
                </Empty>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          {/* Fırsatlar */}
          <Card>
            <CardHeader
              title="Canlı fırsatlar"
              subtitle={status?.auto_mode ? 'Otomatik mod işliyor' : 'Onayını bekliyor'}
              icon={<Radar className="h-4 w-4" />}
              right={
                <Button size="xs" variant="ghost" onClick={() => navigate('/firsatlar')}>
                  Tümü <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              }
            />
            <div className="px-1.5 pb-2">
              {activeSignals.length ? (
                activeSignals.slice(0, 6).map((s) => <SignalRow key={s.id} signal={s} onOpen={() => setOpenSignal(s.id)} />)
              ) : (
                <Empty icon={<Radar className="h-5 w-5" />} title="Şu an aktif fırsat yok">
                  Tarayıcı her mum kapanışında tüm piyasayı yeniden değerlendirir.
                </Empty>
              )}
            </div>
          </Card>

          {/* Piyasa rejimi */}
          <Card>
            <CardHeader
              title="Piyasa rejimi"
              icon={<Activity className="h-4 w-4" />}
              right={regime && <Badge tone={REGIME_TONE[regime.name]} dot>{regime.label}</Badge>}
            />
            {regime ? (
              <div className="space-y-3 px-4 pb-4">
                <div className="flex items-center justify-between text-[12.5px]">
                  <span className="text-muted">BTC trendi</span>
                  <span className="text-fg">{regime.btc_trend || '—'}</span>
                </div>
                <div>
                  <div className="flex items-center justify-between text-[12.5px]">
                    <span className="flex items-center gap-1 text-muted">
                      Piyasa genişliği <InfoTip>EMA50 üzerindeki sembollerin oranı (1 saatlik).</InfoTip>
                    </span>
                    <span className="num text-fg">{regime.breadth != null ? fmtPct(regime.breadth, { digits: 0 }) : '—'}</span>
                  </div>
                  <Meter className="mt-1.5" value={regime.breadth} tone="accent" />
                </div>
                <div>
                  <div className="flex items-center justify-between text-[12.5px]">
                    <span className="flex items-center gap-1 text-muted">
                      Volatilite yüzdeliği <InfoTip>BTC ATR değerinin son dönemdeki yüzdelik sırası. Yüksekse risk azaltılır.</InfoTip>
                    </span>
                    <span className="num text-fg">{regime.vol_pct != null ? fmtPct(regime.vol_pct, { digits: 0 }) : '—'}</span>
                  </div>
                  <Meter className="mt-1.5" value={regime.vol_pct} tone={(regime.vol_pct ?? 0) > 0.85 ? 'down' : (regime.vol_pct ?? 0) > 0.6 ? 'warn' : 'up'} />
                </div>
                <div className="flex items-center justify-between border-t border-line pt-3 text-[12.5px]">
                  <span className="text-muted">Risk çarpanı</span>
                  <span className={clsx('num font-semibold', regime.risk_mult < 1 ? 'text-warn' : 'text-fg')}>×{fmtNum(regime.risk_mult, 2)}</span>
                </div>
                <div className="text-[11px] text-subtle">Güncellendi {fmtRelative(regime.updated_at, now)}</div>
              </div>
            ) : (
              <div className="space-y-2 px-4 pb-4">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-3/4" />
              </div>
            )}
          </Card>

          {/* Tarayıcı ve limitler */}
          <Card>
            <CardHeader title="Tarayıcı ve limitler" icon={<ShieldCheck className="h-4 w-4" />} />
            {status ? (
              <div className="px-4 pb-4">
                <dl className="divide-y divide-line">
                  <div className="kv">
                    <dt>Son tarama</dt>
                    <dd>{fmtRelative(status.scanner.last_scan_at, now)}</dd>
                  </div>
                  <div className="kv">
                    <dt>Sonraki tarama</dt>
                    <dd>{status.scanner.next_scan_at ? fmtCountdown(status.scanner.next_scan_at - now) : '—'}</dd>
                  </div>
                  <div className="kv">
                    <dt>Taranan / evren</dt>
                    <dd>
                      {status.scanner.scanned_symbols} / {status.counts.universe}
                    </dd>
                  </div>
                  <div className="kv">
                    <dt className="flex items-center gap-1">
                      Pozisyon limiti <InfoTip>Sermayeye göre otomatik ölçeklenen etkin limit / ayarlardaki üst limit.</InfoTip>
                    </dt>
                    <dd>
                      {status.counts.open_positions} / {status.limits.effective_max_positions}
                      <span className="text-subtle"> (üst {status.limits.max_positions})</span>
                    </dd>
                  </div>
                  <div className="kv">
                    <dt>İşlem başı risk</dt>
                    <dd>{fmtPct(status.limits.base_risk_pct)}</dd>
                  </div>
                </dl>
                <div className="mt-3 space-y-1.5">
                  {status.breakers.length ? (
                    status.breakers.map((b) => (
                      <div
                        key={b.code}
                        className={clsx(
                          'flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-[12px]',
                          b.active ? 'border-down/30 bg-down/[0.07] text-down' : 'border-line text-muted',
                        )}
                      >
                        <span className="flex items-center gap-2">
                          <span className={clsx('h-1.5 w-1.5 rounded-full', b.active ? 'bg-down' : 'bg-up')} />
                          {b.label}
                        </span>
                        <span className="truncate text-[11.5px]">{b.active ? b.detail || 'aktif' : 'normal'}</span>
                      </div>
                    ))
                  ) : (
                    <div className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-[12px] text-muted">
                      <span className="h-1.5 w-1.5 rounded-full bg-up" /> Tüm devre kesiciler normal
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="px-4 pb-4">
                <Skeleton className="h-20 w-full" />
              </div>
            )}
          </Card>
        </div>
      </div>
      <SignalDrawer signal={sigObj} onClose={() => setOpenSignal(null)} />
    </div>
  );
}
