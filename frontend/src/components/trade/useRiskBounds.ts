import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { SettingsView } from '../../api/types';
import { useApp } from '../../store/app';

let cache: SettingsView | null = null;
let inflight: Promise<SettingsView> | null = null;
const listeners = new Set<(s: SettingsView) => void>();

export function setSettingsCache(s: SettingsView) {
  cache = s;
  for (const l of listeners) l(s);
}

export function loadSettings(force = false): Promise<SettingsView> {
  if (cache && !force) return Promise.resolve(cache);
  if (!inflight) {
    inflight = api
      .settings()
      .then((s) => {
        setSettingsCache(s);
        return s;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function useSettingsCache(): SettingsView | null {
  const [s, setS] = useState<SettingsView | null>(cache);
  useEffect(() => {
    listeners.add(setS);
    if (!cache) void loadSettings().catch(() => undefined);
    return () => {
      listeners.delete(setS);
    };
  }, []);
  return s;
}

export function useRiskBounds() {
  const settings = useSettingsCache();
  const base = useApp((s) => s.status?.limits.base_risk_pct);
  const r = settings?.risk;
  return {
    min: r?.min_risk_pct ?? 0.0025,
    max: r?.max_risk_pct ?? 0.02,
    base: base ?? r?.base_risk_pct ?? 0.0075,
  };
}
