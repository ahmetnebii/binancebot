import clsx from 'clsx';
import { Database, RotateCcw, Users } from 'lucide-react';
import { useState } from 'react';
import { api, withToast } from '../../api/client';
import type { CrowdAnalysis, CrowdStat, CrowdStatus } from '../../api/types';
import { useAsync, useInterval } from '../../lib/hooks';
import { fmtDateTime, fmtNum, pnlClass } from '../../lib/format';
import { toast } from '../../store/app';
import { InfoTip } from '../ui/overlay';
import { Badge, Button, Card, CardHeader } from '../ui/primitives';

const isNum = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const pct = (v: number | null | undefined, d = 2) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '-' : ''}%${Math.abs(v).toFixed(d).replace('.', ',')}` : '—');

function Cell({ s }: { s: CrowdStat }) {
  return (
    <td className="px-2 py-1.5 text-right">
      <div className={clsx('num font-medium', pnlClass(s.mean_pct ?? 0))}>{pct(s.mean_pct)}</div>
      <div className="text-[10.5px] text-subtle">
        {s.n} gözlem{isNum(s.up_pct) ? ` · %${fmtNum(s.up_pct, 0)} yükseldi` : ''}
      </div>
    </td>
  );
}

export function CrowdData() {
  const status = useAsync<CrowdStatus>(() => api.crowdStatus(), []);
  const analysis = useAsync<CrowdAnalysis>(() => api.crowdAnalysis(), []);
  useInterval(() => void status.reload(), 60_000);
  const [busy, setBusy] = useState(false);
  const st = status.data;
  const an = analysis.data;

  const collect = async () => {
    setBusy(true);
    try {
      await withToast(api.crowdCollect(), 'Veri toplanamadı');
      await Promise.all([status.reload(), analysis.reload()]);
      toast.success('Kalabalık verisi güncellendi');
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <CardHeader
          title="Kalabalık verisi toplayıcı"
          subtitle="Binance bu verilerin geçmişini yalnızca ~30 gün verir; bot her saat kaydederek kendi uzun geçmişini biriktirir. Bot açık kaldıkça veri büyür."
          icon={<Database className="h-4 w-4 text-accent" />}
          right={
            <Button size="sm" variant="secondary" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={collect} loading={busy}>
              Şimdi topla
            </Button>
          }
        />
        <div className="grid gap-3 px-4 pb-5 pt-1 sm:grid-cols-4">
          {[
            ['Biriken geçmiş', st ? `${fmtNum(st.days, 1)} gün` : '—'],
            ['Coin', st ? String(st.symbols) : '—'],
            ['Kayıt', st ? `${st.rows.toLocaleString('tr-TR')} saatlik + ${st.funding_rows.toLocaleString('tr-TR')} funding` : '—'],
            ['Son toplama', st?.last_run ? fmtDateTime(st.last_run) : st?.state ?? '—'],
          ].map(([k, v]) => (
            <div key={k} className="rounded-xl border border-line px-3 py-2">
              <div className="label">{k}</div>
              <div className="mt-0.5 text-[13px] font-semibold text-fg">{v}</div>
            </div>
          ))}
          <p className="text-[11.5px] text-subtle sm:col-span-4">
            Toplananlar: açık pozisyon (OI), tüm hesapların long/short oranı, büyük hesapların long/short oranı, agresif alım/satım oranı ve funding.
            {st?.last_error ? <span className="text-down"> Son hata: {st.last_error}</span> : null}
          </p>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader
          title="Kalabalık analizi: kalabalığın tersine işlem kazandırıyor mu?"
          subtitle="Her coinde, ölçüm kendi son 7 gününe göre olağandışı yüksek (z > 1) ve düşük (z < −1) iken sonraki 24 saatte fiyat ne yaptı?"
          icon={<Users className="h-4 w-4 text-accent" />}
        />
        <div className="space-y-3 px-4 pb-5 pt-1">
          {an && (
            <div className={clsx('rounded-xl border px-3 py-2 text-[12px]', an.enough ? 'border-line text-muted' : 'border-warn/30 bg-warn/[0.05] text-warn')}>
              {an.note}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-[12px]">
              <thead>
                <tr className="text-[11px] text-subtle">
                  <th className="px-2 py-1.5 text-left font-medium">Ölçüm</th>
                  <th className="px-2 py-1.5 text-right font-medium">Olağandışı YÜKSEK iken → 24s</th>
                  <th className="px-2 py-1.5 text-right font-medium">Olağandışı DÜŞÜK iken → 24s</th>
                  <th className="px-2 py-1.5 text-right font-medium">
                    Fark (t) <InfoTip>|t| ≥ 2 zayıf, |t| ≥ 3 güçlü kanıt. Az veriyle büyük farklar tesadüf olabilir.</InfoTip>
                  </th>
                  <th className="px-2 py-1.5 text-left font-medium">Yorum</th>
                </tr>
              </thead>
              <tbody>
                {(an?.results ?? []).map((r) => (
                  <tr key={r.feature} className="border-t border-line align-top">
                    <td className="px-2 py-1.5 text-fg">{r.name}</td>
                    <Cell s={r.high} />
                    <Cell s={r.low} />
                    <td className="px-2 py-1.5 text-right">
                      <div className={clsx('num font-medium', pnlClass(r.diff_pct ?? 0))}>{pct(r.diff_pct)}</div>
                      <div className="text-[10.5px] text-subtle">t = {isNum(r.diff_t) ? fmtNum(r.diff_t, 2) : '—'}</div>
                    </td>
                    <td className="px-2 py-1.5">
                      {isNum(r.diff_t) && Math.abs(r.diff_t) >= 3 && an?.enough ? <Badge tone="up">güçlü</Badge> : null} <span className="text-muted">{r.reading}</span>
                    </td>
                  </tr>
                ))}
                {!an?.results?.length && (
                  <tr>
                    <td colSpan={5} className="px-2 py-4 text-center text-subtle">
                      {analysis.loading ? 'Hesaplanıyor…' : 'Henüz veri yok. “Şimdi topla”ya bas.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </Card>
    </div>
  );
}
