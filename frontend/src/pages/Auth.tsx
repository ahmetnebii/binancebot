import clsx from 'clsx';
import { ArrowRight, Lock, ShieldCheck } from 'lucide-react';
import type { FormEvent, ReactNode } from 'react';
import { useState } from 'react';
import { api } from '../api/client';
import { Logo } from '../components/layout/Shell';
import { Field, Input } from '../components/ui/form';
import { Button } from '../components/ui/primitives';
import { useApp } from '../store/app';

function strength(pw: string): { score: number; label: string } {
  let s = 0;
  if (pw.length >= 10) s++;
  if (pw.length >= 14) s++;
  if (/[A-ZÇĞİÖŞÜ]/.test(pw) && /[a-zçğıöşü]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^A-Za-z0-9ÇĞİÖŞÜçğıöşü]/.test(pw)) s++;
  const labels = ['Çok zayıf', 'Zayıf', 'Orta', 'İyi', 'Güçlü', 'Çok güçlü'];
  return { score: s, label: labels[s] };
}

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="bg-grid relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute left-1/2 top-[-20%] h-[520px] w-[820px] -translate-x-1/2 rounded-full bg-accent/10 blur-[120px]" />
      <div className="pointer-events-none absolute bottom-[-25%] right-[-10%] h-[420px] w-[520px] rounded-full bg-proj/10 blur-[120px]" />
      <div className="relative w-full max-w-[420px] animate-scale-in">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <div className="rounded-2xl border border-line bg-surface/90 p-7 shadow-pop backdrop-blur">{children}</div>
        <p className="mt-5 text-center text-[11.5px] text-subtle">
          Kaldıraçlı işlemler yüksek risk içerir. Bu yazılım kazanç garantisi vermez.
        </p>
      </div>
    </div>
  );
}

export function SetupScreen() {
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const st = strength(pw);
  const valid = pw.length >= 10 && pw === pw2;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setErr(null);
    try {
      await api.setup(pw);
      useApp.getState().setAuth('ok');
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Frame>
      <div className="mb-5 flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 text-accent">
          <ShieldCheck className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-[17px] font-semibold text-fg">İlk kurulum</h1>
          <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">
            Paneli korumak için bir şifre oluştur. API anahtarlarını ve Telegram'ı sonra Ayarlar'dan gireceksin.
          </p>
        </div>
      </div>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Panel şifresi" hint="En az 10 karakter. Harf, rakam ve sembol karışımı önerilir.">
          <Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus autoComplete="new-password" />
        </Field>
        {pw && (
          <div>
            <div className="flex gap-1">
              {[0, 1, 2, 3, 4].map((i) => (
                <span
                  key={i}
                  className={clsx(
                    'h-1 flex-1 rounded-full transition-colors',
                    i < st.score ? (st.score >= 4 ? 'bg-up' : st.score >= 3 ? 'bg-accent' : st.score >= 2 ? 'bg-warn' : 'bg-down') : 'bg-elevated',
                  )}
                />
              ))}
            </div>
            <div className="mt-1 text-[11px] text-subtle">Şifre gücü: {st.label}</div>
          </div>
        )}
        <Field label="Şifre (tekrar)" error={pw2 && pw !== pw2 ? 'Şifreler eşleşmiyor' : undefined}>
          <Input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" />
        </Field>
        {err && <div className="rounded-lg border border-down/30 bg-down/[0.07] px-3 py-2 text-[12px] text-down">{err}</div>}
        <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!valid}>
          Şifreyi oluştur ve başla <ArrowRight className="h-4 w-4" />
        </Button>
      </form>
    </Frame>
  );
}

export function LoginScreen() {
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!pw) return;
    setBusy(true);
    setErr(null);
    try {
      await api.login(pw);
      useApp.getState().setAuth('ok');
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
      setPw('');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Frame>
      <div className="mb-5 flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-line bg-elevated text-muted">
          <Lock className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-[17px] font-semibold text-fg">Giriş yap</h1>
          <p className="mt-0.5 text-[12.5px] text-muted">Kontrol paneline erişmek için şifreni gir.</p>
        </div>
      </div>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Şifre">
          <Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus autoComplete="current-password" />
        </Field>
        {err && <div className="rounded-lg border border-down/30 bg-down/[0.07] px-3 py-2 text-[12px] text-down">{err}</div>}
        <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!pw}>
          Giriş yap <ArrowRight className="h-4 w-4" />
        </Button>
      </form>
    </Frame>
  );
}
