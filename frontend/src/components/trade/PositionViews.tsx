import clsx from 'clsx';
import { Bot, ChevronRight, Hand } from 'lucide-react';
import type { Position } from '../../api/types';
import { useNow } from '../../lib/hooks';
import { fmtDateTime, fmtDuration, fmtLev, fmtPct, fmtPrice, fmtQty, fmtR, fmtUsd, pnlClass } from '../../lib/format';
import { navigate } from '../../lib/router';
import { Badge, SideBadge } from '../ui/primitives';
import { Pnl, positionRBar } from '../ui/viz';
import { roeFrac, stopDistFrac } from '../../lib/derive';
import { closeReasonText, strategyTone } from './helpers';

export function SourceIcon({ source }: { source: Position['source'] }) {
  return source === 'auto' ? (
    <span className="inline-flex items-center gap-1 text-[11px] text-up/90" title="Otomatik">
      <Bot className="h-3.5 w-3.5" /> oto
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted" title="Manuel">
      <Hand className="h-3.5 w-3.5" /> manuel
    </span>
  );
}

export function PositionCard({ p }: { p: Position }) {
  const now = useNow(15000);
  return (
    <button
      type="button"
      onClick={() => navigate(`/pozisyon/${p.id}`)}
      className="card group w-full p-4 text-left transition-colors hover:border-line-strong"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-fg">{p.symbol}</span>
            <SideBadge side={p.side} size="sm" />
            <span className="num text-[11.5px] font-semibold text-accent">{fmtLev(p.leverage)}</span>
            <SourceIcon source={p.source} />
          </div>
          <div className="mt-1 truncate text-[11.5px] text-subtle">
            {p.strategy_name} · {fmtDuration(now - p.opened_at)}
          </div>
        </div>
        <div className="text-right">
          <Pnl value={p.pnl_total} size="md" />
          <div className={clsx('num text-[11.5px]', pnlClass(roeFrac(p)))}>ROE {fmtPct(roeFrac(p), { sign: true })}</div>
        </div>
        <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-subtle transition-transform group-hover:translate-x-0.5" />
      </div>
      <div className="mt-3">{positionRBar(p, true)}</div>
      <div className="num mt-2 flex justify-between text-[11px] text-subtle">
        <span>
          Giriş <span className="text-muted">{fmtPrice(p.entry_price)}</span>
        </span>
        <span>
          Mark <span className="text-fg">{fmtPrice(p.mark_price)}</span>
        </span>
        <span className={pnlClass(p.r_now)}>{fmtR(p.r_now)}</span>
      </div>
    </button>
  );
}

export function OpenPositionsTable({ positions }: { positions: Position[] }) {
  const now = useNow(10000);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[980px] border-collapse text-[12.5px]">
        <thead className="table-head border-b border-line">
          <tr>
            <th>Sembol</th>
            <th>Strateji</th>
            <th className="!text-right">Giriş</th>
            <th className="!text-right">Mark</th>
            <th className="!text-right">Büyüklük</th>
            <th className="!text-right">Marjin</th>
            <th className="w-[200px]">İlerleme (R)</th>
            <th className="!text-right">Stop</th>
            <th className="!text-right">K/Z</th>
            <th className="!text-right">Süre</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr
              key={p.id}
              onClick={() => navigate(`/pozisyon/${p.id}`)}
              className="table-row cursor-pointer border-b border-line/70 transition-colors hover:bg-hover/50"
            >
              <td>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-fg">{p.symbol}</span>
                  <SideBadge side={p.side} size="sm" />
                  <span className="num text-[11.5px] font-semibold text-accent">{fmtLev(p.leverage)}</span>
                </div>
              </td>
              <td>
                <div className="flex items-center gap-2">
                  <Badge tone={strategyTone(p.strategy)}>{p.strategy_name}</Badge>
                  <SourceIcon source={p.source} />
                </div>
              </td>
              <td className="num text-right text-muted">{fmtPrice(p.entry_price)}</td>
              <td className="num text-right text-fg">{fmtPrice(p.mark_price)}</td>
              <td className="num text-right text-muted">
                {fmtUsd((p.mark_price ?? p.entry_price) * p.qty, { digits: 0 })}
                <div className="text-[10.5px] text-subtle">{fmtQty(p.qty)}</div>
              </td>
              <td className="num text-right text-muted">{fmtUsd(p.margin)}</td>
              <td>{positionRBar(p, true)}</td>
              <td className="num text-right">
                <span className="text-down">{fmtPrice(p.stop_price)}</span>
                <div className="text-[10.5px] text-subtle">{stopDistFrac(p) != null ? fmtPct(stopDistFrac(p)) : ''}</div>
              </td>
              <td className="text-right">
                <Pnl value={p.pnl_total} size="sm" />
                <div className={clsx('num text-[10.5px]', pnlClass(p.r_now))}>
                  {fmtR(p.r_now)} · {fmtPct(roeFrac(p), { sign: true })}
                </div>
              </td>
              <td className="num text-right text-muted">{fmtDuration(now - p.opened_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ClosedPositionsTable({ positions }: { positions: Position[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[920px] border-collapse text-[12.5px]">
        <thead className="table-head border-b border-line">
          <tr>
            <th>Sembol</th>
            <th>Strateji</th>
            <th>Açılış</th>
            <th>Kapanış</th>
            <th className="!text-right">Süre</th>
            <th className="!text-right">Giriş → Çıkış</th>
            <th className="!text-right">R</th>
            <th className="!text-right">K/Z (USDT)</th>
            <th>Neden</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const r = p.r_final ?? p.r_now;
            return (
              <tr
                key={p.id}
                onClick={() => navigate(`/pozisyon/${p.id}`)}
                className="table-row cursor-pointer border-b border-line/70 transition-colors hover:bg-hover/50"
              >
                <td>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-fg">{p.symbol}</span>
                    <SideBadge side={p.side} size="sm" />
                    <span className="num text-[11.5px] text-muted">{fmtLev(p.leverage)}</span>
                  </div>
                </td>
                <td>
                  <div className="flex items-center gap-2">
                    <span className="text-muted">{p.strategy_name}</span>
                    <SourceIcon source={p.source} />
                  </div>
                </td>
                <td className="num text-muted">{fmtDateTime(p.opened_at)}</td>
                <td className="num text-muted">{fmtDateTime(p.closed_at)}</td>
                <td className="num text-right text-muted">{p.closed_at ? fmtDuration(p.closed_at - p.opened_at) : '—'}</td>
                <td className="num text-right text-muted">
                  {fmtPrice(p.entry_price)} → <span className="text-fg">{fmtPrice(p.exit_price)}</span>
                </td>
                <td className={clsx('num text-right font-semibold', pnlClass(r))}>{fmtR(r)}</td>
                <td className="text-right">
                  <Pnl value={p.pnl_total} size="sm" />
                </td>
                <td>
                  <Badge tone={p.close_reason === 'sl' || p.close_reason === 'liquidation' ? 'down' : p.close_reason === 'tp' ? 'up' : 'neutral'}>
                    {closeReasonText(p.close_reason)}
                  </Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
