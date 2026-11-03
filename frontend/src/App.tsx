import { Loader2 } from 'lucide-react';
import { useEffect } from 'react';
import { api } from './api/client';
import { startWs, stopWs } from './api/ws';
import { Logo, Shell } from './components/layout/Shell';
import { Toaster } from './components/ui/toaster';
import { useInterval } from './lib/hooks';
import { useRoute } from './lib/router';
import { LoginScreen, SetupScreen } from './pages/Auth';
import { Dashboard } from './pages/Dashboard';
import { History } from './pages/History';
import { Logs } from './pages/Logs';
import { Opportunities } from './pages/Opportunities';
import { PositionDetail } from './pages/PositionDetail';
import { Positions } from './pages/Positions';
import { Settings } from './pages/Settings';
import { useApp } from './store/app';

function Splash() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-5">
      <Logo />
      <Loader2 className="h-5 w-5 animate-spin text-subtle" />
    </div>
  );
}

async function bootstrapData() {
  const st = useApp.getState();
  const [status, positions, signals, events] = await Promise.allSettled([
    api.status(),
    api.positions('open', 100),
    api.signals('active'),
    api.events(150),
  ]);
  if (status.status === 'fulfilled') st.setStatus(status.value);
  if (positions.status === 'fulfilled') st.setOpenPositions(positions.value);
  if (signals.status === 'fulfilled') st.setSignals(signals.value);
  if (events.status === 'fulfilled') st.setEvents(events.value);
}

function Authed() {
  const route = useRoute();
  const wsConnected = useApp((s) => s.wsConnected);

  useEffect(() => {
    startWs();
    void bootstrapData();
    return () => stopWs();
  }, []);

  // WS koparsa durumu REST ile tazele
  useInterval(() => {
    if (!wsConnected) void bootstrapData();
  }, 10_000);

  // Yeniden bağlanınca listeleri tazele
  useEffect(() => {
    if (wsConnected) void bootstrapData();
  }, [wsConnected]);

  let page;
  switch (route.page) {
    case 'opportunities':
      page = <Opportunities openId={route.id} />;
      break;
    case 'positions':
      page = <Positions tab={route.tab} />;
      break;
    case 'position':
      page = <PositionDetail id={route.id} />;
      break;
    case 'history':
      page = <History />;
      break;
    case 'settings':
      page = <Settings tab={route.tab} />;
      break;
    case 'logs':
      page = <Logs />;
      break;
    default:
      page = <Dashboard />;
  }
  return <Shell route={route}>{page}</Shell>;
}

export default function App() {
  const auth = useApp((s) => s.auth);
  const setAuth = useApp((s) => s.setAuth);

  useEffect(() => {
    if (auth !== 'unknown') return;
    api
      .authState()
      .then((r) => setAuth(r.authenticated ? 'ok' : r.setup_required ? 'setup' : 'login'))
      .catch(() => setAuth('login'));
  }, [auth, setAuth]);

  return (
    <>
      {auth === 'unknown' ? <Splash /> : auth === 'setup' ? <SetupScreen /> : auth === 'login' ? <LoginScreen /> : <Authed />}
      <Toaster />
    </>
  );
}
