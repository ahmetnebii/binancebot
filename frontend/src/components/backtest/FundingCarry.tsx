import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Coins, Loader2, Radar, RotateCcw, ShieldCheck, Square, Wallet, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { CarryJob, CarryMetrics, CarryResult, FundingRadar } from '../../api/types';
import { useAsync, useInterval, useNow } from '../../lib/hooks';
import { fmtDate, fmtDateTime, fmtDuration, fmtNum, pnlClass } from '../../lib/format';
import { toast, useApp } from '../../store/app';
import { EquityDrawdownChart } from '../analytics/EquityDrawdownChart';
import { InfoTip, useConfirm } from '../ui/overlay';
import { Badge, Button, Card, CardHeader, Segmented } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const pct = (v: number | null | undefined, d = 1, sign = true) =>
  isNum(v) ? `${sign && v > 0 ? '+' : ''}%${v.toFixed(d).replace('.', ',')}`.replace('%-', '-%') : '—';
const coin = (s: string) => s.replace('USDT', '');
const active = (j: { status: string }) => j.status === 'queued' || j.status === 'running';

/* ------------------------------------------------------------------ */
/* Canlı radar + sanal portföy                                          */
/* ------------------------------------------------------------------ */

function RadarCard() {
  const radar = useAsync<FundingRadar>(() => api.fundingRadar(), []);
  useInterval(() => void radar.reload(), 60_000);
  const { confirm, node } = useConfirm();
  const [collecting, setCollecting] = useState(false);
  const d = radar.data;
  const cs = d?.settings;

  const collect = async () => {
    setCollecting(true);
    try {
      await withToast(api.crowdCollect(), 'Veri toplanamadı');
      await radar.reload();
      toast.success('Funding ve kalabalık verisi güncellendi');
    } catch {
      /* toast */
    } finally {
      setCollecting(false);
    }
  };

  const reset = async () => {
    const ok = await confirm({
      title: 'Sanal funding portföyü sıfırlansın mı?',
      message: 'Sanal portföy şu andan itibaren yeniden başlar; birikmiş sonuç silinir.',
      confirmText: 'Sıfırla',
      tone: 'danger',
    });
    if (!ok) return;
    await withToast(api.paperReset(), 'Sıfırlanamadı');
    await radar.reload();
  };

  const rows = (d?.rows ?? []).slice(0, 20);
  const p = d?.paper;
  return (
    <Card className="overflow-hidden">
      {node}
      <CardHeader
        title="Canlı funding radarı"
        subtitle={
          cs
            ? `Kural: geçmiş ${cs.k} funding döneminin (${fmtNum(cs.k / 3, 0)} gün) ortalaması yıllık %${cs.enter_apr} üstündeki en yüksek ${cs.top_n} coin; ortalama %${cs.exit_apr} altına düşünce çık.${cs.source_job ? ' (Geçmiş analizden uygulandı)' : ' (Varsayılan; aşağıdaki analizi çalıştırıp uygulayabilirsin)'}`
            : 'Yükleniyor…'
        }
        icon={<Radar className="h-4 w-4 text-accent" />}
        right={
          <Button size="sm" variant="secondary" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={collect} loading={collecting}>
            Şimdi güncelle
          </Button>
        }
      />
      <div className="space-y-4 px-4 pb-5 pt-1">
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_320px]">
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[620px] text-[12px]">
              <thead>
                <tr className="text-[11px] text-subtle">
                  <th className="px-2 py-1.5 text-left font-medium">Coin</th>
                  <th className="px-2 py-1.5 text-right font-medium">
                    Şu an <InfoTip>Bir sonraki ödemenin tahmini oranı, yıllığa çevrilmiş.</InfoTip>
                  </th>
                  <th className="px-2 py-1.5 text-right font-medium">7 gün</th>
                  <th className="px-2 py-1.5 text-right font-medium">30 gün</th>
                  <th className="px-2 py-1.5 text-right font-medium">Kural ortalaması</th>
                  <th className="px-2 py-1.5 text-left font-medium">Durum</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.symbol} className={clsx('border-t border-line', r.selected && 'bg-up/[0.05]', r.spot === false && 'opacity-50')}>
                    <td className="px-2 py-1.5 font-medium text-fg">{coin(r.symbol)}</td>
                    <td className={clsx('num px-2 py-1.5 text-right', pnlClass(r.current_apr_pct ?? 0))}>{pct(r.current_apr_pct)}</td>
                    <td className={clsx('num px-2 py-1.5 text-right', pnlClass(r.apr_7d_pct ?? 0))}>{pct(r.apr_7d_pct)}</td>
                    <td className={clsx('num px-2 py-1.5 text-right', pnlClass(r.apr_30d_pct ?? 0))}>{pct(r.apr_30d_pct)}</td>
                    <td className={clsx('num px-2 py-1.5 text-right font-semibold', pnlClass(r.trailing_apr_pct ?? 0))}>{pct(r.trailing_apr_pct)}</td>
                    <td className="px-2 py-1.5">
                      {r.spot === false ? (
                        <Badge tone="neutral" title="Binance Spot'ta yok: spot bacağı kurulamaz">Spotta yok</Badge>
                      ) : r.selected ? (
                        <Badge tone="up">Kurala uyuyor</Badge>
                      ) : r.held ? (
                        <Badge tone="warn">Çıkış yakın</Badge>
                      ) : (
                        <span className="text-subtle">—</span>
                      )}
                    </td>
                  </tr>
                ))}
                {!rows.length && (
                  <tr>
                    <td colSpan={6} className="px-2 py-4 text-center text-subtle">
                      {radar.loading ? 'Yükleniyor…' : 'Henüz funding verisi yok. “Şimdi güncelle”ye bas (ilk seferde son 30+ gün doldurulur).'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="rounded-xl border border-line p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-fg">
                <Wallet className="h-4 w-4 text-proj" /> Sanal funding portföyü
                <InfoTip>
                  Radarın kuralını gerçek funding ödemeleriyle, sanal 1.000 USDT ile canlı takip eder. Her 8 saatte bir güncellenir. Komisyonlar
                  düşülür. Bu, stratejinin bugünden sonraki gerçek (ileri) testidir.
                </InfoTip>
              </div>
              <Button size="xs" variant="ghost" onClick={reset}>
                Sıfırla
              </Button>
            </div>
            {p ? (
              <div className="mt-2 space-y-2 text-[12px]">
                <div className="flex items-baseline justify-between">
                  <span className="text-muted">Bakiye</span>
                  <span className={clsx('num text-[18px] font-semibold', pnlClass(p.return_pct))}>{fmtNum(p.equity, 2)} USDT</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Toplam</span>
                  <span className={clsx('num', pnlClass(p.return_pct))}>{pct(p.return_pct, 3)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Yıllık hız</span>
                  <span className="num">{p.apr_pct === null ? 'en az 1 gün gerekir' : pct(p.apr_pct)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Başlangıç</span>
                  <span>{fmtDateTime(p.started_at)} · {p.windows} dönem</span>
                </div>
                <div>
                  <span className="text-muted">Tutulan coinler: </span>
                  {p.holdings.length ? p.holdings.map(coin).join(', ') : <span className="text-subtle">henüz yok — ilk 8 saatlik dönem bitince kurala uyan coinlere girer</span>}
                </div>
                {p.history.length > 0 && (
                  <div className="max-h-36 overflow-y-auto rounded-lg border border-line">
                    <table className="w-full text-[11px]">
                      <tbody>
                        {[...p.history].reverse().slice(0, 15).map((h) => (
                          <tr key={h.t} className="border-t border-line first:border-t-0">
                            <td className="px-2 py-1 text-subtle">{fmtDateTime(h.t)}</td>
                            <td className={clsx('num px-2 py-1 text-right', pnlClass(h.ret_pct))}>{pct(h.ret_pct, 3)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ) : (
              <p className="mt-2 text-[12px] text-subtle">İlk güncellemeden sonra başlar.</p>
            )}
          </div>
        </div>
        <details className="rounded-xl border border-line px-3 py-2 text-[12px] text-muted">
          <summary className="cursor-pointer font-medium text-fg">Gerçekte nasıl uygulanır? (adım adım)</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5">
            <li>Radarda “Kurala uyuyor” olan bir coin seç (ör. 1.000 USDT ayıracaksan coin başına 1.000 / {cs?.top_n ?? 5}).</li>
            <li>
              Binance <b>Spot</b>’ta bu tutarın yaklaşık %75’i kadar coin al (sermayenin kalanı vadeli teminatı içindir, {cs?.leverage ?? 3}x kaldıraçla).
            </li>
            <li>
              Binance <b>USDⓈ-M Vadeli</b>’de aynı coinden <b>aynı adette</b> SHORT aç ({cs?.leverage ?? 3}x, izole marj). Artık fiyat yükselse de düşse de
              toplam değer değişmez.
            </li>
            <li>Funding pozitif olduğu sürece her 8 saatte bir short tarafa ödeme gelir. Kural ortalaması çıkış eşiğinin altına düşünce iki pozisyonu birlikte kapat.</li>
            <li>
              <b>Risk:</b> fiyat çok sert yükselirse short tarafın teminatı azalır; teminata dikkat et (gerekirse spot’taki kârı teminata aktar). Asla yüksek kaldıraç
              kullanma.
            </li>
          </ol>
        </details>
      </div>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Geçmiş analiz                                                        */
/* ------------------------------------------------------------------ */

function MetricRow({ name, sub, m, strong }: { name: string; sub: string; m: CarryMetrics; strong?: boolean }) {
  return (
    <tr className={clsx('border-t border-line', strong && 'bg-accent/[0.05] font-medium')}>
      <td className="px-2 py-1.5">
        <div className="text-fg">{name}</div>
        <div className="text-[11px] text-subtle">{sub}</div>
      </td>
      <td className={clsx('num px-2 py-1.5 text-right font-semibold', pnlClass(m.apr_pct))}>{pct(m.apr_pct)}</td>
      <td className={clsx('num px-2 py-1.5 text-right', pnlClass(m.return_pct))}>{pct(m.return_pct)}</td>
      <td className="num px-2 py-1.5 text-right text-down">{isNum(m.max_drawdown_pct) ? `-%${fmtNum(m.max_drawdown_pct, 2)}` : '—'}</td>
      <td className="num px-2 py-1.5 text-right">{isNum(m.positive_months_pct) ? `%${fmtNum(m.positive_months_pct, 0)}` : '—'}</td>
      <td className="num px-2 py-1.5 text-right">%{fmtNum(m.time_in_pct, 0)}</td>
    </tr>
  );
}

const VERDICT = {
  recommended: { title: 'İstikrarlı funding getirisi bulundu', icon: <CheckCircle2 className="h-5 w-5 text-up" />, cls: 'border-up/30 bg-up/[0.06]' },
  weak: { title: 'Funding getirisi var ama düşük / dalgalı', icon: <AlertTriangle className="h-5 w-5 text-warn" />, cls: 'border-warn/30 bg-warn/[0.06]' },
  none: { title: 'Maliyetlerden sonra getiri çok düşük', icon: <XCircle className="h-5 w-5 text-down" />, cls: 'border-down/30 bg-down/[0.06]' },
};

function CarryResultView({ job, onApply, applying }: { job: CarryJob; onApply: () => void; applying: boolean }) {
  const r = job.result as CarryResult;
  const v = VERDICT[r.verdict];
  const p = r.portfolio;
  return (
    <div className="space-y-4">
      <div className={clsx('flex flex-wrap items-start gap-3 rounded-xl border px-4 py-3', v.cls)}>
        {v.icon}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-fg">{v.title}</span>
            <Badge tone="accent">
              Test dönemi yıllık {pct(p.test.apr_pct)}
            </Badge>
            {job.applied_at && (
              <Badge tone="up">
                <ShieldCheck className="mr-1 inline h-3 w-3" />
                Radara uygulandı
              </Badge>
            )}
          </div>
          <p className="mt-1 text-[12.5px] text-muted">{r.verdict_text}</p>
          <p className="mt-1 text-[11.5px] text-subtle">
            Seçilen kural: geçmiş {p.params.k} dönem ortalaması yıllık %{p.params.enter_apr} üstündeki en yüksek {p.params.top_n} coin, %{p.params.exit_apr}{' '}
            altına inince çık · eğitim {fmtDate(r.period.start)} – {fmtDate(r.period.split)} · test {fmtDate(r.period.split)} – {fmtDate(r.period.end)} · gidiş-dönüş
            maliyet %{fmtNum(r.costs.round_trip_pct, 2)} · {r.costs.leverage}x
          </p>
        </div>
        <Button variant="success" onClick={onApply} loading={applying} disabled={!!job.applied_at}>
          Bu kuralı radara uygula
        </Button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-[12px]">
          <thead>
            <tr className="text-[11px] text-subtle">
              <th className="px-2 py-1 text-left font-medium">Dönem</th>
              <th className="px-2 py-1 text-right font-medium">Yıllık getiri</th>
              <th className="px-2 py-1 text-right font-medium">Toplam</th>
              <th className="px-2 py-1 text-right font-medium">En büyük düşüş</th>
              <th className="px-2 py-1 text-right font-medium">Artı ay oranı</th>
              <th className="px-2 py-1 text-right font-medium">Pozisyonda</th>
            </tr>
          </thead>
          <tbody>
            <MetricRow name="Eğitim (%60)" sub="kural burada seçildi" m={p.train} />
            <MetricRow name="Test (%40)" sub="hiç görülmemiş dönem — asıl sonuç" m={p.test} strong />
          </tbody>
        </table>
      </div>

      {p.equity.length > 1 && (
        <div>
          <div className="label mb-1.5">Portföy değeri (1.000 USDT ile, eğitim + test)</div>
          <EquityDrawdownChart data={p.equity} initial={1000} height={220} />
        </div>
      )}

      {p.monthly_test.length > 0 && (
        <div>
          <div className="label mb-1.5">Test dönemi aylık getiriler</div>
          <div className="flex flex-wrap gap-1.5">
            {p.monthly_test.map((m) => (
              <div key={m.month} className={clsx('rounded-lg border px-2 py-1 text-[11px]', m.return_pct >= 0 ? 'border-up/30 bg-up/[0.05]' : 'border-down/30 bg-down/[0.05]')}>
                <div className="text-subtle">{m.month}</div>
                <div className={clsx('num font-semibold', pnlClass(m.return_pct))}>{pct(m.return_pct, 2)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <details className="rounded-xl border border-line" open>
        <summary className="cursor-pointer px-3 py-2 text-[12.5px] font-medium text-fg">Coin bazında funding getirisi ({r.coins.length} coin)</summary>
        <div className="overflow-x-auto px-1 pb-2">
          <table className="w-full min-w-[700px] text-[12px]">
            <thead>
              <tr className="text-[11px] text-subtle">
                <th className="px-2 py-1 text-left font-medium">Coin</th>
                <th className="px-2 py-1 text-right font-medium">Ort. yıllık funding</th>
                <th className="px-2 py-1 text-right font-medium">Artı ödeme oranı</th>
                <th className="px-2 py-1 text-right font-medium">Son 7 gün</th>
                <th className="px-2 py-1 text-right font-medium">Kurallı test (yıllık)</th>
                <th className="px-2 py-1 text-right font-medium">Hep açık test (yıllık)</th>
                <th className="px-2 py-1 text-right font-medium">Test düşüşü</th>
              </tr>
            </thead>
            <tbody>
              {r.coins.map((c) => (
                <tr key={c.symbol} className="border-t border-line">
                  <td className="px-2 py-1.5 font-medium text-fg">{coin(c.symbol)}</td>
                  <td className={clsx('num px-2 py-1.5 text-right', pnlClass(c.avg_apr_pct))}>{pct(c.avg_apr_pct)}</td>
                  <td className="num px-2 py-1.5 text-right">%{fmtNum(c.positive_pct, 0)}</td>
                  <td className={clsx('num px-2 py-1.5 text-right', pnlClass(c.recent_apr_pct ?? 0))}>{pct(c.recent_apr_pct)}</td>
                  <td className={clsx('num px-2 py-1.5 text-right font-semibold', pnlClass(c.test.apr_pct))}>{pct(c.test.apr_pct)}</td>
                  <td className={clsx('num px-2 py-1.5 text-right', pnlClass(c.always_test.apr_pct))}>{pct(c.always_test.apr_pct)}</td>
                  <td className="num px-2 py-1.5 text-right text-down">-%{fmtNum(c.test.max_drawdown_pct, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <ul className="space-y-1 text-[11.5px] text-subtle">
        {r.warnings.map((w) => (
          <li key={w} className="flex gap-1.5">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            {w}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnalysisCard({ blocked }: { blocked: boolean }) {
  const jobsMap = useApp((s) => s.backtestJobs);
  const [days, setDays] = useState('1095');
  const [fee, setFee] = useState('0.001');
  const [lev, setLev] = useState('3');
  const [coins, setCoins] = useState('1000');
  const [scope, setScope] = useState<'binance' | 'cross'>('binance');
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const now = useNow(1000);
  useAsync(
    () =>
      api.carryJobs().then((l) => {
        const st = useApp.getState();
        l.forEach((j) => st.upsertBacktestJob(j as never));
        return l;
      }),
    [],
  );
  const latest = useMemo(
    () => (Object.values(jobsMap) as unknown as CarryJob[]).filter((j) => j.kind === 'carry').sort((a, b) => b.created_at - a.created_at)[0],
    [jobsMap],
  );
  const full = useAsync<CarryJob | undefined>(
    () => (latest && latest.status === 'done' ? api.carryJob(latest.id) : Promise.resolve(undefined)),
    [latest?.id, latest?.status, latest?.applied_at],
  );
  const shown = full.data && full.data.id === latest?.id ? full.data : latest;
  const running = latest && active(latest);

  const start = async () => {
    setBusy(true);
    try {
      const j = await withToast(api.runCarry({ days: Number(days), top_n: Number(coins), spot_fee: Number(fee), leverage: Number(lev), universe: scope === 'cross' ? 'cross' : coins === '1000' ? 'all' : 'binance' }), 'Analiz başlatılamadı');
      useApp.getState().upsertBacktestJob(j as never);
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!shown) return;
    setApplying(true);
    try {
      await withToast(api.applyCarry(shown.id), 'Uygulanamadı');
      toast.success('Kural canlı funding radarına ve sanal portföye uygulandı');
      full.reload();
    } catch {
      /* toast */
    } finally {
      setApplying(false);
    }
  };

  return (
    <Card className="overflow-hidden">
      <CardHeader
        title="Geçmiş analiz: funding toplama ne kazandırırdı?"
        subtitle="Gerçek funding geçmişiyle (gerekirse analiz sırasında kendisi indirir). Kural eğitim döneminde seçilir, hiç görülmemiş test döneminde ölçülür. Komisyon ve kayma düşülür."
        icon={<Coins className="h-4 w-4 text-accent" />}
      />
      <div className="space-y-4 px-4 pb-5 pt-1">
        {running ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2 text-[12.5px]">
              <span className="flex items-center gap-2 text-fg">
                <Loader2 className="h-4 w-4 animate-spin text-accent" />
                {latest.progress.message || 'Hazırlanıyor…'} · {fmtDuration(now - latest.created_at)}
              </span>
              <Button size="sm" variant="outline-danger" icon={<Square className="h-3.5 w-3.5" />} onClick={() => api.cancelBacktest(latest.id)}>
                Durdur
              </Button>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-elevated">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.round(latest.progress.pct)}%` }} />
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <div className="label mb-1">Geçmiş</div>
              <Segmented value={days} onChange={setDays} options={[{ value: '365', label: '1 yıl' }, { value: '730', label: '2 yıl' }, { value: '1095', label: '3 yıl' }]} />
            </div>
            <div>
              <div className="label mb-1">Spot komisyon</div>
              <Segmented value={fee} onChange={setFee} options={[{ value: '0.001', label: '%0,10' }, { value: '0.00075', label: '%0,075 (BNB)' }]} />
            </div>
            <div>
              <div className="label mb-1 flex items-center gap-1">
                Spot nerede
                <InfoTip>Binance: spot bacağı Binance'te. Herhangi bir borsa: spotu Binance'te olmayan coinler için Bybit / OKX / Gate spot (çapraz borsa; iki borsada bakiye gerekir).</InfoTip>
              </div>
              <Segmented value={scope} onChange={setScope} options={[{ value: 'binance', label: 'Binance' }, { value: 'cross', label: 'Herhangi bir borsa' }]} />
            </div>
            <div>
              <div className="label mb-1">Coin sayısı</div>
              <Segmented value={coins} onChange={setCoins} options={[{ value: '30', label: 'İlk 30' }, { value: '60', label: 'İlk 60' }, { value: '1000', label: 'Tümü' }]} />
            </div>
            <div>
              <div className="label mb-1">Vadeli kaldıraç</div>
              <Segmented value={lev} onChange={setLev} options={[{ value: '2', label: '2x' }, { value: '3', label: '3x' }, { value: '5', label: '5x' }]} />
            </div>
            <Button variant="primary" size="lg" icon={<Coins className="h-4 w-4" />} onClick={start} loading={busy} disabled={blocked} className="ml-auto">
              Funding analizini başlat
            </Button>
            {blocked && <p className="w-full text-right text-[11.5px] text-subtle">Çalışan başka bir iş bitince başlatabilirsin.</p>}
          </div>
        )}
        {!running && shown?.status === 'done' && shown.result && <CarryResultView job={shown} onApply={apply} applying={applying} />}
        {!running && shown?.status === 'failed' && <p className="text-[12px] text-down">Analiz başarısız: {shown.error}</p>}
        {!shown && (
          <p className="text-[12px] leading-relaxed text-muted">
            Funding arbitrajı fiyat tahmini yapmaz: spotta alıp vadelide aynı miktarda short açarak fiyat riskini sıfırlar ve vadeli tarafta 8 saatte bir
            ödenen funding’i toplar. Kriptoda uzun vadede en tutarlı belgelenmiş getiri kaynaklarından biridir; getiri genelde yıllık tek haneli ile
            düşük çift haneli arasındadır ve piyasa coşkuluyken artar.
          </p>
        )}
      </div>
    </Card>
  );
}

export function FundingCarry({ blocked }: { blocked: boolean }) {
  return (
    <div className="space-y-4">
      <RadarCard />
      <AnalysisCard blocked={blocked} />
    </div>
  );
}
