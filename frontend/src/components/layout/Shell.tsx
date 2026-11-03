import clsx from 'clsx';
import {
  AlertOctagon,
  AlertTriangle,
  CandlestickChart,
  FlaskConical,
  History,
  LayoutDashboard,
  LogOut,
  MoreVertical,
  PanelLeftClose,
  PanelLeftOpen,
  Pause,
  Play,
  Radar,
  ScrollText,
  Settings2,
  WifiOff,
  XOctagon,
  Zap,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useId, useState } from 'react';
import { api, withToast } from '../../api/client';
import type { Env, Status } from '../../api/types';
import { fmtPct, fmtUsd, pnlClass } from '../../lib/format';
import { eqDayFrac } from '../../lib/derive';
import { navigate, type Route } from '../../lib/router';
import { toast, useApp } from '../../store/app';
import { MenuItem, Popover, Tooltip, useConfirm } from '../ui/overlay';
import { Button, StatusDot, Switch } from '../ui/primitives';

const NAV: { key: Route['page']; label: string; href: string; icon: ReactNode; mobile?: boolean }[] = [
  { key: 'dashboard', label: 'Panel', href: '/', icon: <LayoutDashboard className="h-[18px] w-[18px]" />, mobile: true },
  { key: 'opportunities', label: 'Fırsatlar', href: '/firsatlar', icon: <Radar className="h-[18px] w-[18px]" />, mobile: true },
  { key: 'positions', label: 'Pozisyonlar', href: '/pozisyonlar', icon: <CandlestickChart className="h-[18px] w-[18px]" />, mobile: true },
  { key: 'history', label: 'Geçmiş', href: '/gecmis', icon: <History className="h-[18px] w-[18px]" />, mobile: true },
  { key: 'backtest', label: 'Backtest', href: '/backtest', icon: <FlaskConical className="h-[18px] w-[18px]" /> },
  { key: 'settings', label: 'Ayarlar', href: '/ayarlar', icon: <Settings2 className="h-[18px] w-[18px]" />, mobile: true },
  { key: 'logs', label: 'Günlük', href: '/gunluk', icon: <ScrollText className="h-[18px] w-[18px]" /> },
];

export const ENV_META: Record<Env, { label: string; cls: string; dot: string; desc: string }> = {
  paper: {
    label: 'SİMÜLASYON',
    cls: 'border-accent/30 bg-accent/10 text-accent',
    dot: 'bg-accent',
    desc: 'Gerçek piyasa verisi, sanal emirler',
  },
  testnet: {
    label: 'TESTNET',
    cls: 'border-warn/30 bg-warn/10 text-warn',
    dot: 'bg-warn',
    desc: 'Binance demo/testnet hesabı',
  },
  live: {
    label: 'CANLI',
    cls: 'border-down/40 bg-down/15 text-down',
    dot: 'bg-down',
    desc: 'Gerçek hesap — gerçek para',
  },
};

export function Logo({ collapsed }: { collapsed?: boolean }) {
  const gid = `lg-${useId().replace(/:/g, '')}`;
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="h-8 w-8 shrink-0">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#5B8CFF" />
            <stop offset="1" stopColor="#A78BFA" />
          </linearGradient>
        </defs>
        <rect width="32" height="32" rx="9" fill="#151A23" />
        <rect x="0.75" y="0.75" width="30.5" height="30.5" rx="8.25" fill="none" stroke={`url(#${gid})`} strokeOpacity=".5" />
        <path d="M8 21.5l5-6 4 3.5 7-9" fill="none" stroke={`url(#${gid})`} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="24" cy="10" r="2.3" fill="#17C987" />
      </svg>
      {!collapsed && (
        <div className="leading-tight">
          <div className="text-[14.5px] font-semibold tracking-tight text-fg">binancebot</div>
          <div className="text-[10.5px] text-subtle">otomatik işlem terminali</div>
        </div>
      )}
    </div>
  );
}

function Sidebar({ route }: { route: Route }) {
  const collapsed = useApp((s) => s.sidebarCollapsed);
  const toggle = useApp((s) => s.toggleSidebar);
  const status = useApp((s) => s.status);
  const counts = status?.counts;
  const active = route.page === 'position' ? 'positions' : route.page;
  return (
    <aside
      className={clsx(
        'sticky top-0 hidden h-screen shrink-0 flex-col border-r border-line bg-surface/60 transition-[width] duration-200 lg:flex',
        collapsed ? 'w-[68px]' : 'w-[232px]',
      )}
    >
      <div className={clsx('flex h-14 items-center border-b border-line', collapsed ? 'justify-center px-2' : 'px-4')}>
        <Logo collapsed={collapsed} />
      </div>
      <nav className="flex-1 space-y-0.5 p-2.5">
        {NAV.map((n) => {
          const isActive = active === n.key;
          const badge =
            n.key === 'opportunities' ? counts?.active_signals : n.key === 'positions' ? counts?.open_positions : undefined;
          const item = (
            <a
              key={n.key}
              href={`#${n.href}`}
              className={clsx(
                'group relative flex h-10 items-center gap-3 rounded-lg text-[13px] font-medium transition-colors',
                collapsed ? 'justify-center px-0' : 'px-3',
                isActive ? 'bg-elevated text-fg ring-1 ring-inset ring-line' : 'text-muted hover:bg-hover/60 hover:text-fg',
              )}
            >
              {isActive && <span className="absolute -left-2.5 top-2 h-6 w-[3px] rounded-r-full bg-accent" />}
              <span className={clsx(isActive ? 'text-accent' : 'text-subtle group-hover:text-muted')}>{n.icon}</span>
              {!collapsed && <span className="flex-1">{n.label}</span>}
              {!!badge && (
                <span
                  className={clsx(
                    'num rounded-md text-[10.5px] font-semibold',
                    collapsed ? 'absolute right-1.5 top-1 px-1' : 'px-1.5 py-px',
                    n.key === 'positions' ? 'bg-up/15 text-up' : 'bg-accent/15 text-accent',
                  )}
                >
                  {badge}
                </span>
              )}
            </a>
          );
          return collapsed ? (
            <Tooltip key={n.key} content={n.label} side="bottom" className="flex w-full">
              {item}
            </Tooltip>
          ) : (
            item
          );
        })}
      </nav>
      <div className="border-t border-line p-2.5">
        {!collapsed && status && (
          <div className="mb-2 rounded-lg border border-line bg-bg/60 px-3 py-2.5">
            <div className="label">Ortam</div>
            <div className="mt-1 flex items-center gap-2 text-[12.5px] text-fg">
              <span className={clsx('h-1.5 w-1.5 rounded-full', ENV_META[status.env].dot)} />
              {ENV_META[status.env].label}
            </div>
            <div className="mt-0.5 text-[11px] text-subtle">{ENV_META[status.env].desc}</div>
          </div>
        )}
        <button
          type="button"
          onClick={toggle}
          className={clsx(
            'flex h-9 w-full items-center gap-2.5 rounded-lg text-[12.5px] text-subtle transition-colors hover:bg-hover hover:text-fg',
            collapsed ? 'justify-center' : 'px-3',
          )}
          aria-label={collapsed ? 'Menüyü genişlet' : 'Menüyü daralt'}
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!collapsed && 'Menüyü daralt'}
        </button>
        {!collapsed && (
          <div className="mt-1 px-3 text-[10.5px] leading-relaxed text-subtle">
            {status && <>sürüm {status.version} · </>}
            grafikler{' '}
            <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer" className="underline decoration-dotted hover:text-muted">
              TradingView Lightweight Charts™
            </a>
          </div>
        )}
      </div>
    </aside>
  );
}

function connState(s: string): 'ok' | 'warn' | 'down' | 'off' {
  if (s === 'ok') return 'ok';
  if (s === 'connecting' || s === 'starting') return 'warn';
  if (s === 'n/a') return 'off';
  return 'down';
}
const CONN_TEXT: Record<string, string> = {
  ok: 'bağlı',
  connecting: 'bağlanıyor',
  starting: 'başlıyor',
  down: 'kopuk',
  error: 'hata',
  no_keys: 'anahtar yok',
  'n/a': 'gerekmiyor',
};

function ConnIndicators({ status }: { status: Status }) {
  const wsConnected = useApp((s) => s.wsConnected);
  const items = [
    { label: 'Sunucu', state: wsConnected ? 'ok' : 'down' },
    { label: 'Piyasa', state: status.connection.market },
    { label: 'Kullanıcı akışı', state: status.connection.user_stream },
    { label: 'Broker', state: status.connection.broker },
  ];
  return (
    <Tooltip
      side="bottom"
      maxWidth={300}
      content={
        <div className="space-y-1.5">
          {items.map((i) => (
            <div key={i.label} className="flex items-center justify-between gap-6">
              <span className="text-muted">{i.label}</span>
              <span className="flex items-center gap-1.5">
                <StatusDot state={connState(i.state)} />
                {CONN_TEXT[i.state] ?? i.state}
              </span>
            </div>
          ))}
          {status.connection.message && <div className="border-t border-line pt-1.5 text-[11.5px] text-muted">{status.connection.message}</div>}
        </div>
      }
    >
      <div className="flex h-8 items-center gap-1.5 rounded-lg border border-line bg-bg/60 px-2.5" tabIndex={0}>
        {items.map((i) => (
          <StatusDot key={i.label} state={connState(i.state)} pulse={i.label === 'Piyasa' && i.state === 'ok'} />
        ))}
        <span className="ml-1 hidden text-[11.5px] text-muted xl:inline">Bağlantılar</span>
      </div>
    </Tooltip>
  );
}

function AutoModeControl({ status, compact }: { status: Status; compact?: boolean }) {
  const { confirm, node } = useConfirm();
  const [busy, setBusy] = useState(false);
  const on = status.auto_mode;
  const toggle = async (next: boolean) => {
    if (next) {
      const live = status.env === 'live';
      const ok = await confirm({
        title: 'Otomatik mod açılsın mı?',
        message: (
          <>
            Sistem, risk kurallarından geçen fırsatlarda <b className="text-fg">senin onayını beklemeden</b> işlem açacak ve
            pozisyonları yönetecek. Ortam: <b className={live ? 'text-down' : 'text-fg'}>{ENV_META[status.env].label}</b>.
          </>
        ),
        confirmText: 'Otomatik modu aç',
        tone: live ? 'danger' : 'success',
        typeToConfirm: live ? 'CANLI' : undefined,
        details: live ? (
          <div className="rounded-lg border border-down/30 bg-down/10 px-3 py-2.5 text-[12.5px] leading-relaxed text-down">
            Canlı hesaptasın. Otomatik mod gerçek parayla işlem yapacak. Kayıplar mümkündür.
          </div>
        ) : undefined,
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      const st = await withToast(api.setAuto(next), 'Otomatik mod değiştirilemedi');
      useApp.getState().setStatus(st); // bildirim sunucudan (WS toast) gelir
    } catch {
      /* toast gösterildi */
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {node}
      <div
        className={clsx(
          'flex items-center gap-3 rounded-xl border transition-all duration-300',
          compact ? 'h-9 px-2.5' : 'h-10 pl-3 pr-2.5',
          on ? 'border-up/40 bg-up/[0.07] shadow-[0_0_0_1px_rgba(23,201,135,0.12),0_0_24px_-8px_rgba(23,201,135,0.5)]' : 'border-line bg-bg/60',
        )}
      >
        {!compact && (
          <div className="leading-tight">
            <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-fg">
              <Zap className={clsx('h-3.5 w-3.5', on ? 'text-up' : 'text-subtle')} />
              Otomatik Mod
            </div>
            <div className={clsx('text-[11px]', on ? 'text-up' : 'text-subtle')}>
              {on ? (status.paused ? 'Açık · duraklatıldı' : 'Açık · sistem işlem açıyor') : 'Kapalı · onayınla işlem'}
            </div>
          </div>
        )}
        {compact && <Zap className={clsx('h-4 w-4', on ? 'text-up' : 'text-subtle')} />}
        <Switch checked={on} onChange={toggle} disabled={busy} size={compact ? 'md' : 'lg'} tone="up" label="Otomatik mod" />
      </div>
    </>
  );
}

function TopBar({ status }: { status: Status | null }) {
  const [menu, setMenu] = useState(false);
  const { confirm, node } = useConfirm();
  const [pauseBusy, setPauseBusy] = useState(false);
  const eq = status?.equity;

  const togglePause = async () => {
    if (!status) return;
    const next = !status.paused;
    if (next) {
      const ok = await confirm({
        title: 'Yeni işlemler durdurulsun mu?',
        message: 'Sistem yeni pozisyon açmayı durdurur. Açık pozisyonlar ve stop/hedef emirleri yönetilmeye devam eder.',
        confirmText: 'Durdur',
        tone: 'danger',
      });
      if (!ok) return;
    }
    setPauseBusy(true);
    try {
      const st = await withToast(api.setPaused(next, next ? 'Kullanıcı tarafından durduruldu' : undefined));
      useApp.getState().setStatus(st);
    } catch {
      /* toast */
    } finally {
      setPauseBusy(false);
    }
  };

  const closeAll = async () => {
    setMenu(false);
    const n = status?.counts.open_positions ?? 0;
    const ok = await confirm({
      title: 'Tüm pozisyonlar kapatılsın mı?',
      message: `${n} açık pozisyon piyasa emriyle hemen kapatılacak. Bu işlem geri alınamaz.`,
      confirmText: 'Hepsini kapat',
      tone: 'danger',
      typeToConfirm: status?.env === 'live' ? 'KAPAT' : undefined,
    });
    if (!ok) return;
    try {
      const r = await withToast(api.closeAll(), 'Pozisyonlar kapatılamadı');
      if (r.closed === 0) toast.info('Kapatılacak pozisyon yok');
    } catch {
      /* toast */
    }
  };

  const logout = async () => {
    setMenu(false);
    try {
      await api.logout();
    } catch {
      /* yok say */
    }
    useApp.getState().setAuth('login');
  };

  return (
    <header className="glass sticky top-0 z-40 border-b border-line">
      {node}
      <div className="flex h-14 items-center gap-3 px-4 lg:px-5">
        <div className="lg:hidden">
          <Logo collapsed />
        </div>
        {status && (
          <span
            className={clsx(
              'inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-bold tracking-[0.08em]',
              ENV_META[status.env].cls,
            )}
          >
            <span className={clsx('h-1.5 w-1.5 rounded-full', ENV_META[status.env].dot, status.env === 'live' && 'animate-live-pulse')} />
            {ENV_META[status.env].label}
          </span>
        )}
        {status && (
          <div className="hidden md:block">
            <ConnIndicators status={status} />
          </div>
        )}
        {eq && (
          <div className="ml-1 hidden items-center gap-5 border-l border-line pl-4 md:flex">
            <div className="leading-tight">
              <div className="label">Özsermaye</div>
              <div className="num text-[14px] font-semibold text-fg">
                {fmtUsd(eq.equity)} <span className="text-[11px] font-medium text-subtle">USDT</span>
              </div>
            </div>
            <div className="leading-tight">
              <div className="label">Bugün</div>
              <div className={clsx('num text-[13px] font-semibold', pnlClass(eq.day_pnl))}>
                {fmtUsd(eq.day_pnl, { sign: true })}{' '}
                <span className="text-[11.5px] font-medium">({fmtPct(eqDayFrac(eq), { sign: true })})</span>
              </div>
            </div>
          </div>
        )}
        <div className="flex-1" />
        {status && (
          <>
            <Button
              size="sm"
              variant={status.paused ? 'success' : 'secondary'}
              icon={status.paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
              onClick={togglePause}
              loading={pauseBusy}
              className="hidden sm:inline-flex"
            >
              {status.paused ? 'Devam et' : 'Durdur'}
            </Button>
            <div className="hidden sm:block">
              <AutoModeControl status={status} />
            </div>
            <div className="sm:hidden">
              <AutoModeControl status={status} compact />
            </div>
          </>
        )}
        <Popover
          open={menu}
          onClose={() => setMenu(false)}
          anchor={
            <button
              type="button"
              onClick={() => setMenu((v) => !v)}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-bg/60 text-muted hover:bg-hover hover:text-fg"
              aria-label="Menü"
            >
              <MoreVertical className="h-4 w-4" />
            </button>
          }
        >
          {status && (
            <div className="sm:hidden">
              <MenuItem
                icon={status.paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                onClick={() => {
                  setMenu(false);
                  void togglePause();
                }}
              >
                {status.paused ? 'Devam et' : 'Yeni işlemleri durdur'}
              </MenuItem>
            </div>
          )}
          <div className="lg:hidden">
            <MenuItem icon={<FlaskConical className="h-4 w-4" />} onClick={() => { setMenu(false); navigate('/backtest'); }}>
              Backtest
            </MenuItem>
          </div>
          <MenuItem icon={<ScrollText className="h-4 w-4" />} onClick={() => { setMenu(false); navigate('/gunluk'); }}>
            Sistem günlüğü
          </MenuItem>
          <MenuItem
            icon={<XOctagon className="h-4 w-4" />}
            danger
            onClick={closeAll}
            disabled={!status || status.counts.open_positions === 0}
          >
            Tüm pozisyonları kapat
          </MenuItem>
          <div className="my-1 border-t border-line" />
          <MenuItem icon={<LogOut className="h-4 w-4" />} onClick={logout}>
            Çıkış yap
          </MenuItem>
        </Popover>
      </div>
    </header>
  );
}

function Banners({ status }: { status: Status | null }) {
  const wsConnected = useApp((s) => s.wsConnected);
  const statusAt = useApp((s) => s.statusAt);
  if (!status) return null;
  const activeBreakers = status.breakers.filter((b) => b.active);
  return (
    <div className="space-y-px">
      {status.data_source === 'synthetic' && (
        <div className="flex items-center gap-2.5 border-b border-warn/25 bg-warn/[0.08] px-5 py-2 text-[12.5px] text-warn">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="font-semibold tracking-wide">GELİŞTİRME VERİSİ</span>
          <span className="text-warn/85">— Sentetik piyasa verisi, gerçek değil. Fiyatlar ve sinyaller sadece arayüz testi içindir.</span>
        </div>
      )}
      {!wsConnected && statusAt > 0 && (
        <div className="flex items-center gap-2.5 border-b border-down/25 bg-down/[0.08] px-5 py-2 text-[12.5px] text-down">
          <WifiOff className="h-4 w-4 shrink-0" />
          Sunucuyla canlı bağlantı koptu — yeniden bağlanılıyor… Görüntülenen veriler güncel olmayabilir.
        </div>
      )}
      {status.paused && (
        <div className="flex items-center gap-2.5 border-b border-warn/25 bg-warn/[0.06] px-5 py-2 text-[12.5px] text-warn">
          <Pause className="h-4 w-4 shrink-0" />
          <span className="font-semibold">Yeni işlemler durduruldu.</span>
          <span className="text-warn/85">{status.paused_reason || 'Açık pozisyonlar yönetilmeye devam ediyor.'}</span>
        </div>
      )}
      {activeBreakers.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-down/25 bg-down/[0.07] px-5 py-2 text-[12.5px] text-down">
          <AlertOctagon className="h-4 w-4 shrink-0" />
          <span className="font-semibold">Devre kesici aktif:</span>
          {activeBreakers.map((b) => (
            <span key={b.code} className="text-down/90">
              {b.label}
              {b.detail ? ` — ${b.detail}` : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function MobileNav({ route }: { route: Route }) {
  const status = useApp((s) => s.status);
  const active = route.page === 'position' ? 'positions' : route.page;
  return (
    <nav className="glass fixed inset-x-0 bottom-0 z-40 flex border-t border-line pb-[env(safe-area-inset-bottom)] lg:hidden">
      {NAV.filter((n) => n.mobile).map((n) => {
        const badge =
          n.key === 'opportunities' ? status?.counts.active_signals : n.key === 'positions' ? status?.counts.open_positions : undefined;
        return (
          <a
            key={n.key}
            href={`#${n.href}`}
            className={clsx(
              'relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10.5px] font-medium',
              active === n.key ? 'text-accent' : 'text-subtle',
            )}
          >
            {n.icon}
            {n.label}
            {!!badge && (
              <span className="num absolute right-[calc(50%-20px)] top-1 rounded bg-accent px-1 text-[9.5px] font-bold text-white">{badge}</span>
            )}
          </a>
        );
      })}
    </nav>
  );
}

export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  const status = useApp((s) => s.status);
  return (
    <div className="flex min-h-screen">
      <Sidebar route={route} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar status={status} />
        <Banners status={status} />
        <main className="flex-1 px-4 pb-24 pt-5 lg:px-6 lg:pb-8">
          <div className="mx-auto w-full max-w-[1720px]">{children}</div>
        </main>
      </div>
      <MobileNav route={route} />
    </div>
  );
}
