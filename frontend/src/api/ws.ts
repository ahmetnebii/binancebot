import { useApp } from '../store/app';
import type { Candle, WsMessage } from './types';

type KlineCb = (c: Candle) => void;

const klineListeners = new Map<string, Set<KlineCb>>();
const keyOf = (symbol: string, interval: string) => `${symbol.toUpperCase()}|${interval}`;

let socket: WebSocket | null = null;
let retry = 0;
let timer: number | null = null;
let stopped = true;

function send(obj: unknown) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(obj));
}

function handle(msg: WsMessage) {
  const st = useApp.getState();
  switch (msg.type) {
    case 'status':
      st.setStatus(msg.data);
      break;
    case 'positions':
      st.setOpenPositions(msg.data);
      break;
    case 'position':
      st.upsertPosition(msg.data);
      break;
    case 'signal':
      st.upsertSignal(msg.data);
      break;
    case 'event':
      st.addEvent(msg.data);
      break;
    case 'toast':
      st.pushToast({ level: msg.data.level, title: msg.data.title, message: msg.data.message });
      break;
    case 'kline': {
      const set = klineListeners.get(keyOf(msg.data.symbol, msg.data.interval));
      if (set) for (const cb of set) cb(msg.data.candle);
      break;
    }
    default:
      break;
  }
}

function connect() {
  if (stopped) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  socket = ws;
  ws.onopen = () => {
    retry = 0;
    useApp.getState().setWsConnected(true);
    // Açık grafik aboneliklerini yeniden gönder
    for (const key of klineListeners.keys()) {
      const [symbol, interval] = key.split('|');
      send({ type: 'chart.sub', symbol, interval });
    }
  };
  ws.onmessage = (ev) => {
    try {
      handle(JSON.parse(ev.data as string) as WsMessage);
    } catch {
      /* bozuk mesajı yok say */
    }
  };
  ws.onclose = (ev) => {
    useApp.getState().setWsConnected(false);
    socket = null;
    if (stopped) return;
    if (ev.code === 4401 || ev.code === 1008) {
      // Oturum yok
      useApp.getState().setAuth('login');
      return;
    }
    const delay = Math.min(15000, 600 * 2 ** retry) + Math.random() * 400;
    retry += 1;
    timer = window.setTimeout(connect, delay);
  };
  ws.onerror = () => {
    try {
      ws.close();
    } catch {
      /* zaten kapalı */
    }
  };
}

export function startWs() {
  if (!stopped) return;
  stopped = false;
  connect();
}

export function stopWs() {
  stopped = true;
  if (timer) window.clearTimeout(timer);
  timer = null;
  if (socket) {
    try {
      socket.close();
    } catch {
      /* yok say */
    }
  }
  socket = null;
}

/** Bir sembol/aralık için canlı mum güncellemelerine abone olur. Dönen fonksiyon aboneliği kaldırır. */
export function subscribeKline(symbol: string, interval: string, cb: KlineCb): () => void {
  const key = keyOf(symbol, interval);
  let set = klineListeners.get(key);
  if (!set) {
    set = new Set();
    klineListeners.set(key, set);
    send({ type: 'chart.sub', symbol: symbol.toUpperCase(), interval });
  }
  set.add(cb);
  return () => {
    const s = klineListeners.get(key);
    if (!s) return;
    s.delete(cb);
    if (s.size === 0) {
      klineListeners.delete(key);
      send({ type: 'chart.unsub', symbol: symbol.toUpperCase(), interval });
    }
  };
}
