import { create } from 'zustand';
import type { EventItem, EventLevel, Position, Signal, Status } from '../api/types';

export type AuthState = 'unknown' | 'setup' | 'login' | 'ok';

export interface Toast {
  id: number;
  level: EventLevel;
  title: string;
  message?: string;
  ts: number;
}

interface AppState {
  auth: AuthState;
  status: Status | null;
  statusAt: number;
  openPositions: Position[];
  positionsAt: number;
  signals: Record<string, Signal>;
  signalsLoaded: boolean;
  events: EventItem[];
  wsConnected: boolean;
  toasts: Toast[];
  sidebarCollapsed: boolean;
  mobileNavOpen: boolean;
  /** Pozisyon güncellemeleri için artan sayaç (detay sayfaları yeniden çeksin diye). */
  positionRev: Record<string, number>;

  setAuth: (a: AuthState) => void;
  setStatus: (s: Status) => void;
  setOpenPositions: (p: Position[]) => void;
  upsertPosition: (p: Position) => void;
  setSignals: (list: Signal[]) => void;
  upsertSignal: (s: Signal) => void;
  setEvents: (list: EventItem[]) => void;
  addEvent: (e: EventItem) => void;
  setWsConnected: (v: boolean) => void;
  pushToast: (t: Omit<Toast, 'id' | 'ts'>) => void;
  dismissToast: (id: number) => void;
  toggleSidebar: () => void;
  setMobileNav: (v: boolean) => void;
}

let toastSeq = 1;

function readCollapsed(): boolean {
  try {
    return localStorage.getItem('bb.sidebar') === '1';
  } catch {
    return false;
  }
}

export const useApp = create<AppState>((set, get) => ({
  auth: 'unknown',
  status: null,
  statusAt: 0,
  openPositions: [],
  positionsAt: 0,
  signals: {},
  signalsLoaded: false,
  events: [],
  wsConnected: false,
  toasts: [],
  sidebarCollapsed: readCollapsed(),
  mobileNavOpen: false,
  positionRev: {},

  setAuth: (auth) => set({ auth }),
  setStatus: (status) => set({ status, statusAt: Date.now() }),
  setOpenPositions: (openPositions) => set({ openPositions, positionsAt: Date.now() }),
  upsertPosition: (p) => {
    const list = get().openPositions;
    let next: Position[];
    if (p.status === 'OPEN') {
      const idx = list.findIndex((x) => x.id === p.id);
      next = idx >= 0 ? list.map((x) => (x.id === p.id ? p : x)) : [p, ...list];
    } else {
      next = list.filter((x) => x.id !== p.id);
    }
    set({
      openPositions: next,
      positionRev: { ...get().positionRev, [p.id]: (get().positionRev[p.id] ?? 0) + 1 },
    });
  },
  setSignals: (list) => {
    const map: Record<string, Signal> = {};
    for (const s of list) map[s.id] = s;
    set({ signals: { ...get().signals, ...map }, signalsLoaded: true });
  },
  upsertSignal: (s) => set({ signals: { ...get().signals, [s.id]: s } }),
  setEvents: (events) => set({ events: events.slice(0, 500) }),
  addEvent: (e) => {
    const list = get().events;
    if (list.some((x) => x.id === e.id)) return;
    set({ events: [e, ...list].slice(0, 500) });
  },
  setWsConnected: (wsConnected) => set({ wsConnected }),
  pushToast: (t) => {
    const now = Date.now();
    if (get().toasts.some((x) => x.title === t.title && x.message === t.message && now - x.ts < 4000)) return;
    const id = toastSeq++;
    const toast: Toast = { ...t, id, ts: Date.now() };
    set({ toasts: [...get().toasts, toast].slice(-5) });
    const ttl = t.level === 'error' ? 8000 : t.level === 'warning' ? 6500 : 4500;
    window.setTimeout(() => get().dismissToast(id), ttl);
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  toggleSidebar: () => {
    const v = !get().sidebarCollapsed;
    try {
      localStorage.setItem('bb.sidebar', v ? '1' : '0');
    } catch {
      /* depolama kapalı olabilir */
    }
    set({ sidebarCollapsed: v });
  },
  setMobileNav: (mobileNavOpen) => set({ mobileNavOpen }),
}));

export const toast = {
  success: (title: string, message?: string) => useApp.getState().pushToast({ level: 'success', title, message }),
  info: (title: string, message?: string) => useApp.getState().pushToast({ level: 'info', title, message }),
  warning: (title: string, message?: string) => useApp.getState().pushToast({ level: 'warning', title, message }),
  error: (title: string, message?: string) => useApp.getState().pushToast({ level: 'error', title, message }),
};
