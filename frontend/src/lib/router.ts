import { useEffect, useState } from 'react';

export type Route =
  | { page: 'dashboard' }
  | { page: 'opportunities'; id?: string }
  | { page: 'positions'; tab?: string }
  | { page: 'position'; id: string }
  | { page: 'history' }
  | { page: 'settings'; tab?: string }
  | { page: 'logs' };

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#/, '').replace(/^\/+/, '');
  const [a, b] = path.split('/').map((x) => decodeURIComponent(x));
  switch (a) {
    case 'firsatlar':
      return { page: 'opportunities', id: b || undefined };
    case 'pozisyonlar':
      return { page: 'positions', tab: b || undefined };
    case 'pozisyon':
      return b ? { page: 'position', id: b } : { page: 'positions' };
    case 'gecmis':
      return { page: 'history' };
    case 'ayarlar':
      return { page: 'settings', tab: b || undefined };
    case 'gunluk':
      return { page: 'logs' };
    default:
      return { page: 'dashboard' };
  }
}

export function navigate(path: string) {
  const target = path.startsWith('#') ? path : `#${path.startsWith('/') ? path : `/${path}`}`;
  if (location.hash !== target) location.hash = target;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseHash(location.hash));
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}
