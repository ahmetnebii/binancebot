import clsx from 'clsx';
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  FlaskConical,
  KeyRound,
  Lock,
  Plug,
  Radar,
  RotateCcw,
  Save,
  Send,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';
import { api, withToast } from '../api/client';
import type { Env, RiskSettings, ScannerSettings, SettingsView, StrategySettings } from '../api/types';
import { loadSettings, setSettingsCache, useSettingsCache } from '../components/trade/useRiskBounds';
import { ChipsInput, Field, Input, NumberInput, Textarea, Toggle } from '../components/ui/form';
import { InfoTip, useConfirm } from '../components/ui/overlay';
import { Badge, Button, Card, Segmented, Skeleton } from '../components/ui/primitives';
import { Tabs } from '../components/ui/viz';
import { fmtCompact, fmtLev, fmtPct, fmtUsd } from '../lib/format';
import { navigate } from '../lib/router';
import { toast, useApp } from '../store/app';

type TabKey = 'baglanti' | 'telegram' | 'risk' | 'tarama' | 'simulasyon' | 'guvenlik';

function Section({ title, description, children, right }: { title: ReactNode; description?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <Card className="p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold text-fg">{title}</h3>
          {description && <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-muted">{description}</p>}
        </div>
        {right}
      </div>
      {children}
    </Card>
  );
}

function SaveBar({ dirty, busy, onSave, onReset }: { dirty: boolean; busy: boolean; onSave: () => void; onReset: () => void }) {
  return (
    <div
      className={clsx(
        'sticky bottom-20 z-20 flex items-center justify-between gap-3 rounded-xl border px-4 py-3 shadow-pop transition-all lg:bottom-4',
        dirty ? 'border-accent/40 bg-elevated' : 'pointer-events-none translate-y-2 border-transparent bg-transparent opacity-0',
      )}
    >
      <span className="text-[12.5px] text-muted">Kaydedilmemiş değişiklikler var</span>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" onClick={onReset} icon={<RotateCcw className="h-3.5 w-3.5" />}>
          Geri al
        </Button>
        <Button size="sm" variant="primary" onClick={onSave} loading={busy} icon={<Save className="h-3.5 w-3.5" />}>
          Kaydet
        </Button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* Bağlantı                                                          */
/* ---------------------------------------------------------------- */

const ENV_CARDS: { env: Env; title: string; icon: ReactNode; desc: string; tone: string }[] = [
  {
    env: 'paper',
    title: 'Simülasyon',
    icon: <FlaskConical className="h-5 w-5" />,
    desc: 'Gerçek piyasa verisiyle sanal işlem. API anahtarı gerekmez, risk yoktur. Stratejileri izlemek için ideal başlangıç.',
    tone: 'accent',
  },
  {
    env: 'testnet',
    title: 'Testnet',
    icon: <Plug className="h-5 w-5" />,
    desc: "Binance'in demo/testnet hesabı. Emir mekaniğini gerçek borsa API'siyle test eder; fiyatlar gerçek piyasadan farklı olabilir.",
    tone: 'warn',
  },
  {
    env: 'live',
    title: 'Canlı',
    icon: <Zap className="h-5 w-5" />,
    desc: 'Gerçek Binance Futures hesabı ve gerçek para. Sadece doğrulanmış stratejiler ve küçük sermayeyle başlayın.',
    tone: 'down',
  },
];

function ConnectionTab({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const c = s.connection;
  const { confirm, node } = useConfirm();
  const [keyType, setKeyType] = useState(c.key_type);
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<{ ok: boolean; message: string; equity?: number } | null>(null);
  const [adv, setAdv] = useState(false);
  const [urls, setUrls] = useState({
    live_rest: c.live_rest,
    live_ws: c.live_ws,
    testnet_rest: c.testnet_rest,
    testnet_ws: c.testnet_ws,
    user_stream_path: c.user_stream_path,
    market_stream_path: c.market_stream_path ?? '/market/stream',
  });
  useEffect(() => {
    setKeyType(c.key_type);
    setUrls({
      live_rest: c.live_rest,
      live_ws: c.live_ws,
      testnet_rest: c.testnet_rest,
      testnet_ws: c.testnet_ws,
      user_stream_path: c.user_stream_path,
      market_stream_path: c.market_stream_path ?? '/market/stream',
    });
  }, [c.key_type, c.live_rest, c.live_ws, c.testnet_rest, c.testnet_ws, c.user_stream_path, c.market_stream_path]);

  const switchEnv = async (env: Env) => {
    if (env === c.env) return;
    const live = env === 'live';
    const ok = await confirm({
      title: `${ENV_CARDS.find((e) => e.env === env)?.title} ortamına geçilsin mi?`,
      message: live
        ? 'Canlı ortamda sistem GERÇEK PARAYLA işlem yapar. Otomatik mod güvenlik için kapatılacak; açık simülasyon pozisyonları bu ortamda görünmez.'
        : 'Ortam değiştiğinde otomatik mod güvenlik için kapatılır. Diğer ortamdaki pozisyonlar kendi ortamında kalır.',
      confirmText: 'Ortamı değiştir',
      tone: live ? 'danger' : 'primary',
      typeToConfirm: live ? 'CANLI' : undefined,
      details:
        env !== 'paper' && !c.api_key_set ? (
          <div className="rounded-lg border border-warn/30 bg-warn/[0.07] px-3 py-2.5 text-[12px] text-warn">
            Henüz API anahtarı kaydedilmemiş. Ortamı değiştirdikten sonra anahtarlarını aşağıdan girmen gerekiyor.
          </div>
        ) : undefined,
    });
    if (!ok) return;
    setBusy('env');
    try {
      const v = await withToast(api.saveConnection({ env, confirm_live: live ? 'CANLI' : undefined }), 'Ortam değiştirilemedi');
      onSaved(v);
      setTest(null);
      toast.success('Ortam değiştirildi', `${ENV_CARDS.find((e) => e.env === env)?.title} ortamı aktif. Otomatik mod kapalı.`);
      const st = await api.status().catch(() => null);
      if (st) useApp.getState().setStatus(st);
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const saveKeys = async () => {
    const body: Record<string, unknown> = { key_type: keyType };
    if (apiKey.trim()) body.api_key = apiKey.trim();
    if (keyType === 'hmac' && apiSecret.trim()) body.api_secret = apiSecret.trim();
    if (keyType === 'ed25519' && privateKey.trim()) body.private_key = privateKey.trim();
    setBusy('keys');
    try {
      const v = await withToast(api.saveConnection(body), 'Anahtarlar kaydedilemedi');
      onSaved(v);
      setApiKey('');
      setApiSecret('');
      setPrivateKey('');
      toast.success('Anahtarlar kaydedildi', 'Sunucuda şifrelenmiş olarak saklanıyor.');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const clearKeys = async () => {
    const ok = await confirm({
      title: 'Kayıtlı anahtarlar silinsin mi?',
      message: 'API anahtarı, secret ve özel anahtar sunucudan kalıcı olarak silinir. Testnet/Canlı ortamda bağlantı kesilir.',
      confirmText: 'Anahtarları sil',
      tone: 'danger',
    });
    if (!ok) return;
    setBusy('clear');
    try {
      const v = await withToast(api.saveConnection({ clear: ['api_key', 'api_secret', 'private_key'] }), 'Anahtarlar silinemedi');
      onSaved(v);
      setTest(null);
      toast.info('Anahtarlar silindi');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    setBusy('test');
    try {
      const r = await api.testConnection();
      setTest(r);
    } catch (e) {
      setTest({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  };

  const saveUrls = async () => {
    setBusy('urls');
    try {
      const v = await withToast(api.saveConnection(urls), 'Adresler kaydedilemedi');
      onSaved(v);
      toast.success('Gelişmiş ayarlar kaydedildi');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const canSave = apiKey.trim() || (keyType === 'hmac' ? apiSecret.trim() : privateKey.trim()) || keyType !== c.key_type;

  return (
    <div className="space-y-4">
      {node}
      <Section title="Çalışma ortamı" description="Botun hangi hesapta işlem yapacağını seçin. Önerilen yol: Simülasyon → Testnet → küçük sermayeyle Canlı.">
        <div className="grid gap-3 md:grid-cols-3">
          {ENV_CARDS.map((e) => {
            const sel = c.env === e.env;
            return (
              <button
                key={e.env}
                type="button"
                disabled={busy === 'env'}
                onClick={() => void switchEnv(e.env)}
                className={clsx(
                  'relative flex flex-col items-start rounded-xl border p-4 text-left transition-all',
                  sel
                    ? e.tone === 'down'
                      ? 'border-down/50 bg-down/[0.07] shadow-[0_0_0_1px_rgba(240,75,91,0.25)]'
                      : e.tone === 'warn'
                        ? 'border-warn/50 bg-warn/[0.06] shadow-[0_0_0_1px_rgba(245,165,36,0.2)]'
                        : 'border-accent/50 bg-accent/[0.07] shadow-glow'
                    : 'border-line bg-bg/40 hover:border-line-strong hover:bg-hover/40',
                )}
              >
                <div className="flex w-full items-center gap-2.5">
                  <span
                    className={clsx(
                      'flex h-9 w-9 items-center justify-center rounded-lg border',
                      e.tone === 'down' ? 'border-down/30 text-down' : e.tone === 'warn' ? 'border-warn/30 text-warn' : 'border-accent/30 text-accent',
                    )}
                  >
                    {e.icon}
                  </span>
                  <span className="text-[14px] font-semibold text-fg">{e.title}</span>
                  {sel && (
                    <span className="ml-auto inline-flex items-center gap-1 text-[11.5px] font-medium text-up">
                      <CheckCircle2 className="h-4 w-4" /> Aktif
                    </span>
                  )}
                </div>
                <p className="mt-3 text-[12px] leading-relaxed text-muted">{e.desc}</p>
              </button>
            );
          })}
        </div>
      </Section>

      <Section
        title="Binance API anahtarları"
        description="Anahtarlar sunucuda şifrelenmiş olarak saklanır ve tarayıcıya asla geri gönderilmez. Testnet ve canlı için ayrı anahtar kullanın."
        right={
          c.api_key_set ? (
            <Badge tone="up" dot>
              Kayıtlı {c.api_key_hint}
            </Badge>
          ) : (
            <Badge tone="warn" dot>
              Anahtar yok
            </Badge>
          )
        }
      >
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            <Field label="Anahtar türü" hint="Ed25519 önerilir: özel anahtar sende kalır, Binance'e sadece açık anahtarı yüklersin.">
              <Segmented
                value={keyType}
                onChange={setKeyType}
                options={[
                  { value: 'hmac', label: 'HMAC (API Key + Secret)' },
                  { value: 'ed25519', label: 'Ed25519' },
                ]}
              />
            </Field>
            <Field label="API Key" hint={c.api_key_set ? `Kayıtlı anahtar: ${c.api_key_hint}. Değiştirmek için yenisini girin.` : undefined}>
              <Input mono value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={c.api_key_set ? c.api_key_hint : 'API anahtarını yapıştırın'} autoComplete="off" spellCheck={false} />
            </Field>
            {keyType === 'hmac' ? (
              <Field label="API Secret" hint={c.api_secret_set ? 'Secret kayıtlı. Değiştirmek için yenisini girin.' : undefined}>
                <Input mono type="password" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} placeholder={c.api_secret_set ? '•••••••••••••••• (kayıtlı)' : 'API secret'} autoComplete="new-password" spellCheck={false} />
              </Field>
            ) : (
              <Field label="Ed25519 özel anahtar (PEM)" hint={c.private_key_set ? 'Özel anahtar kayıtlı. Değiştirmek için yenisini yapıştırın.' : 'BEGIN PRIVATE KEY ile başlayan PEM içeriği'}>
                <Textarea value={privateKey} onChange={(e) => setPrivateKey(e.target.value)} placeholder={c.private_key_set ? '(kayıtlı — gösterilmez)' : '-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----'} spellCheck={false} />
              </Field>
            )}
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" icon={<KeyRound className="h-4 w-4" />} onClick={saveKeys} loading={busy === 'keys'} disabled={!canSave}>
                Anahtarları kaydet
              </Button>
              <Button variant="secondary" icon={<Plug className="h-4 w-4" />} onClick={runTest} loading={busy === 'test'}>
                Bağlantıyı test et
              </Button>
              <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={clearKeys} loading={busy === 'clear'} disabled={!c.api_key_set && !c.api_secret_set && !c.private_key_set}>
                Temizle
              </Button>
            </div>
            {test && (
              <div
                className={clsx(
                  'flex items-start gap-2.5 rounded-lg border px-3.5 py-3 text-[12.5px]',
                  test.ok ? 'border-up/30 bg-up/[0.07] text-up' : 'border-down/30 bg-down/[0.07] text-down',
                )}
              >
                {test.ok ? <CheckCircle2 className="mt-px h-4 w-4 shrink-0" /> : <XCircle className="mt-px h-4 w-4 shrink-0" />}
                <div>
                  <div className="font-semibold">{test.ok ? 'Bağlantı başarılı' : 'Bağlantı başarısız'}</div>
                  <div className="mt-0.5 opacity-90">{test.message}</div>
                  {test.equity != null && <div className="num mt-0.5">Özsermaye: {fmtUsd(test.equity)} USDT</div>}
                </div>
              </div>
            )}
          </div>
          <div className="space-y-3 rounded-xl border border-line bg-bg/50 p-4 text-[12.5px] leading-relaxed text-muted">
            <div className="flex items-center gap-2 font-semibold text-fg">
              <ShieldCheck className="h-4 w-4 text-up" /> Güvenlik kontrol listesi
            </div>
            {[
              ['Çekim (Withdraw) yetkisi KAPALI', 'Bot para çekemez; anahtar ele geçse bile fonlarınız transfer edilemez.'],
              ['IP kısıtlaması AÇIK', "Sadece botun çalıştığı sunucunun sabit IP'si. Binance kısıtsız anahtarlarla işlemi engelleyebilir."],
              ['Sadece Okuma + Vadeli İşlem', 'Spot/Margin ve evrensel transfer yetkileri gerekmedikçe kapalı.'],
              ['Ayrı alt hesap (öneri)', 'Botun pozisyonları manuel işlemlerinizle karışmaz.'],
            ].map(([t, d]) => (
              <div key={t} className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-up" />
                <div>
                  <div className="text-fg">{t}</div>
                  <div className="text-[11.5px] text-subtle">{d}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <button type="button" onClick={() => setAdv((v) => !v)} className="mt-5 inline-flex items-center gap-1.5 text-[12.5px] font-medium text-muted hover:text-fg">
          <ChevronDown className={clsx('h-4 w-4 transition-transform', adv && 'rotate-180')} />
          Gelişmiş: sunucu adresleri
        </button>
        {adv && (
          <div className="mt-3 space-y-3 rounded-xl border border-line bg-bg/40 p-4">
            <p className="text-[12px] text-subtle">Binance adres değiştirirse buradan güncelleyebilirsiniz. Emin değilseniz değiştirmeyin.</p>
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Canlı REST">
                <Input mono value={urls.live_rest} onChange={(e) => setUrls({ ...urls, live_rest: e.target.value })} />
              </Field>
              <Field label="Canlı WebSocket">
                <Input mono value={urls.live_ws} onChange={(e) => setUrls({ ...urls, live_ws: e.target.value })} />
              </Field>
              <Field label="Testnet REST">
                <Input mono value={urls.testnet_rest} onChange={(e) => setUrls({ ...urls, testnet_rest: e.target.value })} />
              </Field>
              <Field label="Testnet WebSocket">
                <Input mono value={urls.testnet_ws} onChange={(e) => setUrls({ ...urls, testnet_ws: e.target.value })} />
              </Field>
            </div>
            <div className="rounded-lg border border-line bg-bg/40 p-3">
              <p className="mb-3 text-[12px] text-subtle">Binance WebSocket yolları (2026 yapısı). Yalnızca Binance değiştirirse düzenleyin.</p>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Piyasa akışı yolu">
                  <Input mono value={urls.market_stream_path} onChange={(e) => setUrls({ ...urls, market_stream_path: e.target.value })} />
                </Field>
                <Field label="Kullanıcı akışı yolu" hint="{listenKey} yer tutucusu bağlantıda gerçek anahtarla değiştirilir.">
                  <Input mono value={urls.user_stream_path} onChange={(e) => setUrls({ ...urls, user_stream_path: e.target.value })} />
                </Field>
              </div>
            </div>
            <Button size="sm" variant="secondary" icon={<Save className="h-3.5 w-3.5" />} loading={busy === 'urls'} onClick={saveUrls}>
              Adresleri kaydet
            </Button>
          </div>
        )}
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* Telegram                                                          */
/* ---------------------------------------------------------------- */

function TelegramTab({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const t = s.telegram;
  const [token, setToken] = useState('');
  const [chatId, setChatId] = useState(t.chat_id);
  const [enabled, setEnabled] = useState(t.enabled);
  const [notifySignals, setNotifySignals] = useState(t.notify_signals);
  const [notifyTrades, setNotifyTrades] = useState(t.notify_trades);
  const [chats, setChats] = useState<{ chat_id: string; title: string }[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    setChatId(t.chat_id);
    setEnabled(t.enabled);
    setNotifySignals(t.notify_signals);
    setNotifyTrades(t.notify_trades);
  }, [t.chat_id, t.enabled, t.notify_signals, t.notify_trades]);

  const save = async (extra: Record<string, unknown> = {}) => {
    setBusy('save');
    try {
      const body: Record<string, unknown> = { enabled, chat_id: chatId.trim(), notify_signals: notifySignals, notify_trades: notifyTrades, ...extra };
      if (token.trim()) body.bot_token = token.trim();
      const v = await withToast(api.saveTelegram(body), 'Telegram ayarları kaydedilemedi');
      onSaved(v);
      setToken('');
      toast.success('Telegram ayarları kaydedildi');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const findChats = async () => {
    setBusy('chats');
    try {
      const list = await withToast(api.telegramChats(), 'Sohbetler alınamadı');
      setChats(list);
      if (!list.length) toast.info('Sohbet bulunamadı', 'Önce Telegram\'da botunuza /start yazın, sonra tekrar deneyin.');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  const sendTest = async () => {
    setBusy('test');
    try {
      const r = await api.testTelegram();
      if (r.ok) toast.success('Test mesajı gönderildi', r.message);
      else toast.error('Test mesajı gönderilemedi', r.message);
    } catch (e) {
      toast.error('Test mesajı gönderilemedi', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const dirty = token.trim() !== '' || chatId !== t.chat_id || enabled !== t.enabled || notifySignals !== t.notify_signals || notifyTrades !== t.notify_trades;

  return (
    <div className="space-y-4">
      <Section
        title="Telegram bildirimleri ve uzaktan kontrol"
        description="İşlem açılış/kapanışları, yeni fırsatlar ve uyarılar Telegram'a gönderilir. Botu Telegram'dan da durdurabilir veya otomatik modu açıp kapatabilirsiniz."
        right={
          t.bot_token_set ? (
            <Badge tone={t.enabled ? 'up' : 'neutral'} dot>
              {t.enabled ? 'Etkin' : 'Kapalı'} · {t.bot_token_hint}
            </Badge>
          ) : (
            <Badge tone="warn" dot>
              Token yok
            </Badge>
          )
        }
      >
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-4">
            <Toggle checked={enabled} onChange={setEnabled} label="Telegram bildirimleri" description="Kapalıyken hiçbir mesaj gönderilmez." />
            <Field label="Bot token" hint={t.bot_token_set ? `Kayıtlı token: ${t.bot_token_hint}. Değiştirmek için yenisini girin.` : '@BotFather ile oluşturduğunuz botun token\'ı'}>
              <Input mono type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={t.bot_token_set ? `${t.bot_token_hint} (kayıtlı)` : '123456789:AA…'} autoComplete="new-password" spellCheck={false} />
            </Field>
            <Field
              label="Chat ID"
              hint="Sadece bu sohbetten gelen komutlar kabul edilir."
              right={
                <Button size="xs" variant="ghost" onClick={findChats} loading={busy === 'chats'} disabled={!t.bot_token_set}>
                  Chat ID bul
                </Button>
              }
            >
              <Input mono value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="ör. 123456789" />
            </Field>
            {chats && chats.length > 0 && (
              <div className="rounded-lg border border-line bg-bg/50 p-1.5">
                {chats.map((c) => (
                  <button
                    key={c.chat_id}
                    type="button"
                    onClick={() => setChatId(c.chat_id)}
                    className={clsx('flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-[12.5px] hover:bg-hover', chatId === c.chat_id && 'bg-hover')}
                  >
                    <span className="text-fg">{c.title}</span>
                    <span className="num text-muted">{c.chat_id}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="space-y-1 rounded-lg border border-line p-3">
              <Toggle checked={notifyTrades} onChange={setNotifyTrades} label="İşlem bildirimleri" description="Pozisyon açılış, hedef, stop ve kapanışları." />
              <Toggle checked={notifySignals} onChange={setNotifySignals} label="Fırsat bildirimleri" description="Otomatik mod kapalıyken yeni fırsatlar onayınıza gelir." />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" icon={<Save className="h-4 w-4" />} onClick={() => void save()} loading={busy === 'save'} disabled={!dirty}>
                Kaydet
              </Button>
              <Button variant="secondary" icon={<Send className="h-4 w-4" />} onClick={sendTest} loading={busy === 'test'} disabled={!t.bot_token_set || !t.chat_id}>
                Test mesajı gönder
              </Button>
              {t.bot_token_set && (
                <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => void save({ clear_token: true })}>
                  Token'ı sil
                </Button>
              )}
            </div>
          </div>
          <div className="rounded-xl border border-line bg-bg/50 p-4 text-[12.5px] leading-relaxed text-muted">
            <div className="mb-2 flex items-center gap-2 font-semibold text-fg">
              <Bell className="h-4 w-4 text-accent" /> Kurulum (2 dakika)
            </div>
            <ol className="list-decimal space-y-1.5 pl-4">
              <li>
                Telegram'da <span className="num text-fg">@BotFather</span>'a <span className="num text-fg">/newbot</span> yazın ve botunuzu oluşturun.
              </li>
              <li>Verilen token'ı yukarıya yapıştırıp kaydedin.</li>
              <li>
                Kendi botunuza <span className="num text-fg">/start</span> yazın.
              </li>
              <li>"Chat ID bul" ile sohbetinizi seçin, kaydedin ve test mesajı gönderin.</li>
            </ol>
            <div className="mt-3 border-t border-line pt-3 text-[11.5px] text-subtle">
              Komutlar: /durum, /pozisyonlar, /otomatik_ac, /otomatik_kapat, /durdur, /devam
            </div>
          </div>
        </div>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* Risk & Kasa                                                       */
/* ---------------------------------------------------------------- */

const PROFILE_META: Record<'temkinli' | 'dengeli' | 'agresif', { title: string; desc: string; tone: string }> = {
  temkinli: { title: 'Temkinli', desc: 'Düşük risk, daha az eşzamanlı pozisyon, düşük kaldıraç tavanları.', tone: 'up' },
  dengeli: { title: 'Dengeli', desc: 'Önerilen başlangıç. Risk ve büyüme arasında denge.', tone: 'accent' },
  agresif: { title: 'Agresif', desc: 'Daha yüksek işlem riski. Düşüşler de büyür — deneyim ister.', tone: 'down' },
};

type NumKey = { [K in keyof RiskSettings]: RiskSettings[K] extends number ? K : never }[keyof RiskSettings];

function RiskField({
  label,
  k,
  r,
  set,
  pct,
  suffix,
  hint,
  min,
  max,
  decimals,
}: {
  label: string;
  k: NumKey;
  r: RiskSettings;
  set: (k: NumKey, v: number) => void;
  pct?: boolean;
  suffix?: string;
  hint?: string;
  min?: number;
  max?: number;
  decimals?: number;
}) {
  return (
    <Field
      label={
        <>
          {label}
          {hint && <InfoTip>{hint}</InfoTip>}
        </>
      }
    >
      <NumberInput
        value={r[k] as number}
        onChange={(v) => v !== null && set(k, v)}
        scale={pct ? 100 : 1}
        suffix={pct ? '%' : suffix}
        min={min}
        max={max}
        decimals={decimals ?? (pct ? 2 : 2)}
      />
    </Field>
  );
}

function LeverageExplainer({ r }: { r: RiskSettings }) {
  const equityLive = useApp((s) => s.status?.equity?.equity);
  const E = equityLive && equityLive > 0 ? equityLive : 1000;
  const d = 0.02;
  const cost = 0.0015;
  const mmr = 0.005;
  const notional = (E * r.base_risk_pct) / (d + cost);
  const maxMargin = E * r.max_margin_per_trade_pct;
  const levNeeded = Math.max(1, Math.ceil(notional / maxMargin));
  const levLiq = Math.floor(1 / (r.liq_buffer_k * d + mmr));
  const cap = Math.min(levLiq, r.cap_large, r.global_max_leverage);
  const lev = Math.min(levNeeded, cap);
  const finalNotional = levNeeded > cap ? maxMargin * cap : notional;
  return (
    <div className="rounded-xl border border-accent/25 bg-accent/[0.05] p-4">
      <div className="flex items-center gap-2 text-[13px] font-semibold text-fg">
        <SlidersHorizontal className="h-4 w-4 text-accent" /> Sistem kaldıracı nasıl belirler?
      </div>
      <ol className="mt-2.5 list-decimal space-y-1 pl-5 text-[12.5px] leading-relaxed text-muted">
        <li>
          <span className="text-fg">Önce risk:</span> her işlemde özsermayenin <b className="text-fg">{fmtPct(r.base_risk_pct)}</b>'i riske edilir (çarpanlarla ayarlanır).
        </li>
        <li>
          <span className="text-fg">Sonra büyüklük:</span> büyüklük = risk ÷ (stop mesafesi + maliyet). Stop genişse pozisyon küçülür.
        </li>
        <li>
          <span className="text-fg">En son kaldıraç:</span> pozisyonu marjin bütçesine (özsermayenin {fmtPct(r.max_margin_per_trade_pct, { digits: 0 })}'i) sığdıran <b className="text-fg">en düşük</b> kaldıraç seçilir;
          likidasyon stop'tan en az <b className="text-fg">{r.liq_buffer_k.toLocaleString('tr-TR')} kat</b> uzakta kalacak şekilde tavanlanır.
        </li>
      </ol>
      <div className="num mt-3 rounded-lg border border-line bg-bg/60 px-3 py-2.5 text-[12px] text-muted">
        Örnek ({equityLive ? 'güncel özsermaye' : '1.000 USDT'} · %2 stop): büyüklük <span className="text-fg">{fmtUsd(finalNotional)} USDT</span> · kaldıraç{' '}
        <span className="font-semibold text-accent">{fmtLev(lev)}</span> · marjin <span className="text-fg">{fmtUsd(finalNotional / lev)}</span> · stop'ta zarar{' '}
        <span className="text-down">{fmtUsd(finalNotional * (d + cost))} USDT</span>
      </div>
      <p className="mt-2.5 text-[11.5px] leading-relaxed text-subtle">
        Tüm hesaplar <b className="text-muted">güncel özsermayeye oranlıdır</b>: başlangıç sermayen ne olursa olsun sistem kendini ölçekler; kazandıkça
        pozisyonlar büyür, kaybettikçe otomatik küçülür. Küçük sermayede eşzamanlı pozisyon sayısı da otomatik azaltılır.
      </p>
    </div>
  );
}

function RiskTab({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const [r, setR] = useState<RiskSettings>(s.risk);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => setR(s.risk), [s.risk]);
  const dirty = JSON.stringify(r) !== JSON.stringify(s.risk);
  const set = (k: NumKey, v: number) => setR((x) => ({ ...x, [k]: v }));

  const applyProfile = async (profile: 'temkinli' | 'dengeli' | 'agresif') => {
    setBusy(profile);
    try {
      const v = await withToast(api.saveRisk({ profile }), 'Profil uygulanamadı');
      onSaved(v);
      toast.success(`${PROFILE_META[profile].title} profil uygulandı`);
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    setBusy('save');
    try {
      const v = await withToast(api.saveRisk(r), 'Risk ayarları kaydedilemedi');
      onSaved(v);
      toast.success('Risk ayarları kaydedildi');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <Section title="Risk profili" description="Hazır profiller tüm risk parametrelerini birlikte ayarlar. Tek tek değiştirirseniz profil 'Özel' olur.">
        <div className="grid gap-3 md:grid-cols-3">
          {(['temkinli', 'dengeli', 'agresif'] as const).map((p) => {
            const prof = s.profiles[p] ?? {};
            const sel = s.risk.profile === p;
            const meta = PROFILE_META[p];
            return (
              <button
                key={p}
                type="button"
                onClick={() => void applyProfile(p)}
                disabled={busy !== null}
                className={clsx(
                  'flex flex-col items-start rounded-xl border p-4 text-left transition-all',
                  sel ? 'border-accent/50 bg-accent/[0.07] shadow-glow' : 'border-line bg-bg/40 hover:border-line-strong hover:bg-hover/40',
                )}
              >
                <div className="flex w-full items-center gap-2">
                  <span className={clsx('h-2 w-2 rounded-full', meta.tone === 'up' ? 'bg-up' : meta.tone === 'down' ? 'bg-down' : 'bg-accent')} />
                  <span className="text-[14px] font-semibold text-fg">{meta.title}</span>
                  {p === 'dengeli' && !sel && <Badge tone="neutral">önerilen</Badge>}
                  {sel && (
                    <span className="ml-auto inline-flex items-center gap-1 text-[11.5px] font-medium text-up">
                      <CheckCircle2 className="h-4 w-4" /> Seçili
                    </span>
                  )}
                </div>
                <p className="mt-2 text-[12px] text-muted">{meta.desc}</p>
                <div className="num mt-3 grid w-full grid-cols-3 gap-2 border-t border-line pt-3 text-[11.5px]">
                  <div>
                    <div className="text-subtle">Risk</div>
                    <div className="text-fg">{prof.base_risk_pct != null ? fmtPct(prof.base_risk_pct) : '—'}</div>
                  </div>
                  <div>
                    <div className="text-subtle">Pozisyon</div>
                    <div className="text-fg">{prof.max_positions ?? '—'}</div>
                  </div>
                  <div>
                    <div className="text-subtle">Maks. düşüş</div>
                    <div className="text-fg">{prof.max_drawdown_pct != null ? fmtPct(prof.max_drawdown_pct, { digits: 0 }) : '—'}</div>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
        {s.risk.profile === 'ozel' && <div className="mt-3 text-[12px] text-muted">Şu an özel (elle ayarlanmış) bir profil kullanılıyor.</div>}
      </Section>

      <LeverageExplainer r={r} />

      <Section title="İşlem riski" description="Her işlemde riske edilen tutar ve sinyal kalite eşikleri.">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <RiskField label="Taban işlem riski" k="base_risk_pct" r={r} set={set} pct hint="Stop tetiklenirse kaybedilecek tutarın özsermayeye oranı." min={0.0005} max={0.05} />
          <RiskField label="Minimum risk" k="min_risk_pct" r={r} set={set} pct min={0.0001} max={0.05} />
          <RiskField label="Maksimum risk" k="max_risk_pct" r={r} set={set} pct hint="Çarpanlar ne olursa olsun aşılmaz. %2 üstü önerilmez." min={0.0005} max={0.05} />
          <RiskField label="Min. beklenti (EV)" k="min_ev_r" r={r} set={set} suffix="R" hint="Beklenen değeri bunun altındaki fırsatlar reddedilir." min={-1} max={2} />
          <RiskField label="Maks. giriş kayması" k="max_entry_drift_r" r={r} set={set} suffix="R" hint="Fiyat sinyal seviyesinden bu kadar uzaklaştıysa işleme girilmez." min={0} max={2} />
        </div>
      </Section>

      <Section title="Kaldıraç" description="Kaldıraç otomatik hesaplanır; bu değerler yalnızca güvenlik tavanlarıdır.">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
          <RiskField label="Likidasyon tamponu (k)" k="liq_buffer_k" r={r} set={set} suffix="×" hint="Likidasyon mesafesi ≥ k × stop mesafesi." min={1} max={10} decimals={1} />
          <RiskField label="İşlem başı maks. marjin" k="max_margin_per_trade_pct" r={r} set={set} pct min={0.01} max={1} decimals={0} />
          <RiskField label="Tavan · major (BTC/ETH)" k="cap_major" r={r} set={set} suffix="x" min={1} max={125} decimals={0} />
          <RiskField label="Tavan · büyük altcoin" k="cap_large" r={r} set={set} suffix="x" min={1} max={125} decimals={0} />
          <RiskField label="Tavan · diğer" k="cap_other" r={r} set={set} suffix="x" min={1} max={125} decimals={0} />
          <RiskField label="Genel tavan" k="global_max_leverage" r={r} set={set} suffix="x" min={1} max={125} decimals={0} />
        </div>
      </Section>

      <Section title="Portföy limitleri" description="Aynı anda alınabilecek toplam risk ve korelasyon sınırları.">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <RiskField label="Maks. eşzamanlı pozisyon" k="max_positions" r={r} set={set} min={1} max={30} decimals={0} />
          <RiskField label="Aynı yönde maks." k="max_same_side" r={r} set={set} min={1} max={30} decimals={0} />
          <RiskField label="Portföy ısısı limiti" k="max_portfolio_heat_pct" r={r} set={set} pct hint="Tüm stoplar tetiklenirse toplam zarar sınırı." min={0.001} max={0.5} />
          <RiskField label="Toplam marjin limiti" k="max_total_margin_pct" r={r} set={set} pct min={0.05} max={1} decimals={0} />
          <RiskField label="Maks. efektif kaldıraç" k="max_effective_leverage" r={r} set={set} suffix="x" hint="Toplam büyüklük / özsermaye." min={0.5} max={50} decimals={1} />
          <RiskField label="Korelasyon kümesi riski" k="max_cluster_risk_pct" r={r} set={set} pct min={0.001} max={0.5} />
          <RiskField label="Korelasyon eşiği" k="cluster_corr" r={r} set={set} hint="Bu değerin üstünde korelasyonu olan coinler tek küme sayılır." min={0} max={1} />
          <div className="flex items-end pb-1">
            <Toggle
              checked={r.capital_tiers}
              onChange={(v) => setR({ ...r, capital_tiers: v })}
              label="Sermayeye göre ölçekle"
              description="Küçük sermayede pozisyon sayısını otomatik azaltır."
            />
          </div>
        </div>
      </Section>

      <Section title="Devre kesiciler" description="Bu sınırlar aşılınca sistem yeni işlem açmayı otomatik durdurur.">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <RiskField label="Günlük zarar limiti" k="daily_loss_pct" r={r} set={set} pct min={0.005} max={0.5} />
          <RiskField label="Haftalık zarar limiti" k="weekly_loss_pct" r={r} set={set} pct min={0.01} max={0.5} />
          <RiskField label="Maksimum düşüş" k="max_drawdown_pct" r={r} set={set} pct hint="Zirveden bu kadar düşüşte tam durdurma; manuel onay gerekir." min={0.02} max={0.9} decimals={0} />
          <RiskField label="Kayıp serisi" k="loss_streak_count" r={r} set={set} suffix="işlem" min={2} max={50} decimals={0} />
          <RiskField label="Seri sonrası bekleme" k="loss_streak_cooldown_min" r={r} set={set} suffix="dk" min={0} max={1440} decimals={0} />
        </div>
      </Section>
      <SaveBar dirty={dirty} busy={busy === 'save'} onSave={save} onReset={() => setR(s.risk)} />
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* Tarama & Stratejiler                                              */
/* ---------------------------------------------------------------- */

function ScannerTab({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const [sc, setSc] = useState<ScannerSettings>(s.scanner);
  const [st, setSt] = useState<StrategySettings>(s.strategies);
  const [busy, setBusy] = useState(false);
  useEffect(() => setSc(s.scanner), [s.scanner]);
  useEffect(() => setSt(s.strategies), [s.strategies]);
  const dirtySc = JSON.stringify(sc) !== JSON.stringify(s.scanner);
  const dirtySt = JSON.stringify(st) !== JSON.stringify(s.strategies);

  const save = async () => {
    setBusy(true);
    try {
      let v: SettingsView | null = null;
      if (dirtySc) v = await withToast(api.saveScanner(sc), 'Tarama ayarları kaydedilemedi');
      if (dirtySt) v = await withToast(api.saveStrategies(st), 'Strateji ayarları kaydedilemedi');
      if (v) onSaved(v);
      toast.success('Ayarlar kaydedildi');
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Section title="Piyasa evreni" description="Hangi USDT perpetual kontratların taranacağını belirleyen filtreler. Evren saatte bir yenilenir.">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <Field label="Min. 24 sa hacim" hint={`≈ ${fmtCompact(sc.min_quote_volume)} USDT`}>
            <NumberInput value={sc.min_quote_volume} onChange={(v) => v !== null && setSc({ ...sc, min_quote_volume: v })} suffix="USDT" min={0} decimals={0} />
          </Field>
          <Field label="Min. listelenme yaşı">
            <NumberInput value={sc.min_age_days} onChange={(v) => v !== null && setSc({ ...sc, min_age_days: Math.round(v) })} suffix="gün" min={0} max={365} decimals={0} />
          </Field>
          <Field label="Maks. sembol sayısı">
            <NumberInput value={sc.max_symbols} onChange={(v) => v !== null && setSc({ ...sc, max_symbols: Math.round(v) })} min={5} max={600} decimals={0} />
          </Field>
          <Field label="Maks. spread">
            <NumberInput value={sc.max_spread_bps} onChange={(v) => v !== null && setSc({ ...sc, max_spread_bps: v })} suffix="bps" min={0} max={100} decimals={1} />
          </Field>
          <Field label="Fırsat geçerlilik süresi">
            <NumberInput value={sc.signal_ttl_min} onChange={(v) => v !== null && setSc({ ...sc, signal_ttl_min: Math.round(v) })} suffix="dk" min={1} max={240} decimals={0} />
          </Field>
        </div>
        <Field label="Kara liste" hint="Bu sembollerde asla işlem açılmaz. Enter veya virgülle ekleyin." className="mt-4">
          <ChipsInput value={sc.blacklist} onChange={(v) => setSc({ ...sc, blacklist: v })} placeholder="ör. XYZUSDT" />
        </Field>
      </Section>

      <Section title="Stratejiler" description="Her strateji kendi koşullarıyla fırsat üretir; risk motoru hepsini aynı kurallarla değerlendirir.">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className={clsx('rounded-xl border p-4', st.s1_enabled ? 'border-accent/30 bg-accent/[0.04]' : 'border-line')}>
            <Toggle
              checked={st.s1_enabled}
              onChange={(v) => setSt({ ...st, s1_enabled: v })}
              label={
                <span className="flex items-center gap-2">
                  S1 · Trend Kırılımı <Badge tone="accent">15 dk</Badge>
                </span>
              }
              description="Üst zaman dilimi trendi yönünde, hacimle onaylanan Donchian kırılımları. Stop ATR tabanlı, hedef merdivenli."
            />
            <div className="mt-4 grid grid-cols-3 gap-3">
              <Field label="Donchian penceresi">
                <NumberInput value={st.s1_donchian} onChange={(v) => v !== null && setSt({ ...st, s1_donchian: Math.round(v) })} suffix="bar" min={10} max={200} decimals={0} disabled={!st.s1_enabled} />
              </Field>
              <Field label="Hacim z-skoru eşiği">
                <NumberInput value={st.s1_vol_z} onChange={(v) => v !== null && setSt({ ...st, s1_vol_z: v })} min={0} max={10} decimals={1} disabled={!st.s1_enabled} />
              </Field>
              <Field label="Stop (ATR katı)">
                <NumberInput value={st.s1_atr_mult} onChange={(v) => v !== null && setSt({ ...st, s1_atr_mult: v })} suffix="×" min={0.5} max={6} decimals={1} disabled={!st.s1_enabled} />
              </Field>
            </div>
          </div>
          <div className={clsx('rounded-xl border p-4', st.s2_enabled ? 'border-proj/30 bg-proj/[0.04]' : 'border-line')}>
            <Toggle
              checked={st.s2_enabled}
              onChange={(v) => setSt({ ...st, s2_enabled: v })}
              label={
                <span className="flex items-center gap-2">
                  S2 · Göreli Güç Rotasyonu <Badge tone="proj">1 sa</Badge>
                </span>
              }
              description="BTC etkisinden arındırılmış, volatiliteye göre ölçeklenmiş momentumla en güçlü/zayıf coinler. Rejime göre long/short."
            />
            <div className="mt-4 grid grid-cols-3 gap-3">
              <Field label="Seçilecek coin sayısı">
                <NumberInput value={st.s2_top_n} onChange={(v) => v !== null && setSt({ ...st, s2_top_n: Math.round(v) })} min={1} max={20} decimals={0} disabled={!st.s2_enabled} />
              </Field>
            </div>
          </div>
        </div>
      </Section>
      <SaveBar
        dirty={dirtySc || dirtySt}
        busy={busy}
        onSave={save}
        onReset={() => {
          setSc(s.scanner);
          setSt(s.strategies);
        }}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* Simülasyon                                                        */
/* ---------------------------------------------------------------- */

function PaperTab({ s, onSaved }: { s: SettingsView; onSaved: (v: SettingsView) => void }) {
  const [bal, setBal] = useState<number | null>(s.paper.starting_balance);
  const [busy, setBusy] = useState<string | null>(null);
  const { confirm, node } = useConfirm();
  useEffect(() => setBal(s.paper.starting_balance), [s.paper.starting_balance]);

  const save = async (reset: boolean) => {
    if (reset) {
      const ok = await confirm({
        title: 'Simülasyon sıfırlansın mı?',
        message: `Sanal bakiye ${fmtUsd(bal ?? 0)} USDT olarak yeniden başlatılır; açık simülasyon pozisyonları kapatılır.`,
        confirmText: 'Sıfırla',
        tone: 'danger',
      });
      if (!ok) return;
    }
    setBusy(reset ? 'reset' : 'save');
    try {
      const v = await withToast(api.savePaper({ starting_balance: bal ?? undefined, reset }), 'Simülasyon ayarı kaydedilemedi');
      onSaved(v);
      if (!reset) toast.success('Başlangıç bakiyesi kaydedildi');
    } catch {
      /* toast */
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      {node}
      <Section
        title="Simülasyon (kağıt işlem)"
        description="Simülasyon modunda gerçek piyasa verisi kullanılır; emirler komisyon ve kayma dahil sanal olarak gerçekleştirilir. Stratejileri gerçek parayla denemeden önce burada izleyin."
      >
        <div className="grid gap-5 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          <div className="space-y-4">
            <Field label="Başlangıç bakiyesi" hint="Sermayeniz netleşmediyse sorun değil: tüm büyüklükler bakiyeye oranlı hesaplanır.">
              <NumberInput value={bal} onChange={setBal} suffix="USDT" min={10} max={10_000_000} decimals={2} />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" icon={<Save className="h-4 w-4" />} onClick={() => void save(false)} loading={busy === 'save'} disabled={bal === s.paper.starting_balance}>
                Kaydet
              </Button>
              <Button variant="outline-danger" icon={<RotateCcw className="h-4 w-4" />} onClick={() => void save(true)} loading={busy === 'reset'}>
                Bu bakiyeyle sıfırla
              </Button>
            </div>
          </div>
          <dl className="divide-y divide-line rounded-xl border border-line px-4">
            <div className="kv">
              <dt>Kayma varsayımı</dt>
              <dd>{s.paper.slippage_bps} bps</dd>
            </div>
            <div className="kv">
              <dt>Taker komisyonu</dt>
              <dd>{fmtPct(s.paper.taker_fee, { digits: 3 })}</dd>
            </div>
            <div className="kv">
              <dt>Maker komisyonu</dt>
              <dd>{fmtPct(s.paper.maker_fee, { digits: 3 })}</dd>
            </div>
            <div className="kv">
              <dt>Funding</dt>
              <dd>gerçek oranlarla uygulanır</dd>
            </div>
          </dl>
        </div>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- */
/* Güvenlik                                                          */
/* ---------------------------------------------------------------- */

function SecurityTab() {
  const [cur, setCur] = useState('');
  const [nw, setNw] = useState('');
  const [nw2, setNw2] = useState('');
  const [busy, setBusy] = useState(false);
  const err = nw && nw.length < 10 ? 'En az 10 karakter' : nw2 && nw !== nw2 ? 'Şifreler eşleşmiyor' : undefined;
  const submit = async () => {
    setBusy(true);
    try {
      await withToast(api.changePassword(cur, nw), 'Şifre değiştirilemedi');
      toast.success('Şifre değiştirildi');
      setCur('');
      setNw('');
      setNw2('');
    } catch {
      /* toast */
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <Section title="Panel şifresi" description="Bu panel gerçek parayı yönetebilir; güçlü ve benzersiz bir şifre kullanın.">
        <div className="grid max-w-xl gap-4">
          <Field label="Mevcut şifre">
            <Input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />
          </Field>
          <Field label="Yeni şifre" error={err}>
            <Input type="password" value={nw} onChange={(e) => setNw(e.target.value)} autoComplete="new-password" />
          </Field>
          <Field label="Yeni şifre (tekrar)">
            <Input type="password" value={nw2} onChange={(e) => setNw2(e.target.value)} autoComplete="new-password" />
          </Field>
          <div>
            <Button variant="primary" icon={<Lock className="h-4 w-4" />} onClick={submit} loading={busy} disabled={!cur || !nw || nw.length < 10 || nw !== nw2}>
              Şifreyi değiştir
            </Button>
          </div>
        </div>
      </Section>
      <Section title="Erişim güvenliği" description="Paneli internete doğrudan açmayın.">
        <ul className="space-y-2 text-[12.5px] leading-relaxed text-muted">
          {[
            'Panel varsayılan olarak yalnızca sunucunun kendisinden (127.0.0.1) erişilebilir.',
            'Uzaktan erişim için SSH tüneli kullanın: ssh -L 8000:localhost:8000 kullanici@sunucu',
            'Alan adıyla erişecekseniz HTTPS (ör. Caddy + Let\'s Encrypt) arkasında yayınlayın.',
            'API anahtarları sunucuda şifreli saklanır; ana şifreleme anahtarını (data/master.key) ayrıca yedekleyin.',
          ].map((t) => (
            <li key={t} className="flex gap-2">
              <Shield className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------- */

export function Settings({ tab }: { tab?: string }) {
  const cached = useSettingsCache();
  const [s, setS] = useState<SettingsView | null>(cached);
  const [err, setErr] = useState<string | null>(null);
  const active: TabKey = (['baglanti', 'telegram', 'risk', 'tarama', 'simulasyon', 'guvenlik'] as TabKey[]).includes(tab as TabKey)
    ? (tab as TabKey)
    : 'baglanti';

  useEffect(() => {
    loadSettings(true)
      .then(setS)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(() => {
    if (cached) setS(cached);
  }, [cached]);

  const onSaved = (v: SettingsView) => {
    setSettingsCache(v);
    setS(v);
  };

  const items = useMemo(
    () => [
      { value: 'baglanti' as TabKey, label: 'Bağlantı', icon: <Plug className="h-4 w-4" /> },
      { value: 'telegram' as TabKey, label: 'Telegram', icon: <Send className="h-4 w-4" /> },
      { value: 'risk' as TabKey, label: 'Risk & Kasa', icon: <ShieldCheck className="h-4 w-4" /> },
      { value: 'tarama' as TabKey, label: 'Tarama & Stratejiler', icon: <Radar className="h-4 w-4" /> },
      { value: 'simulasyon' as TabKey, label: 'Simülasyon', icon: <FlaskConical className="h-4 w-4" /> },
      { value: 'guvenlik' as TabKey, label: 'Güvenlik', icon: <Lock className="h-4 w-4" /> },
    ],
    [],
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[20px] font-semibold tracking-tight text-fg">Ayarlar</h1>
        <p className="mt-0.5 text-[12.5px] text-muted">Bağlantı, anahtarlar, bildirimler ve risk kuralları. Tüm değişiklikler sunucuya kaydedilir.</p>
      </div>
      <Tabs value={active} onChange={(v) => navigate(`/ayarlar/${v}`)} items={items} />
      {!s ? (
        err ? (
          <Card className="p-5 text-[12.5px] text-down">Ayarlar yüklenemedi: {err}</Card>
        ) : (
          <div className="space-y-3">
            <Skeleton className="h-40 w-full rounded-xl" />
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        )
      ) : active === 'baglanti' ? (
        <ConnectionTab s={s} onSaved={onSaved} />
      ) : active === 'telegram' ? (
        <TelegramTab s={s} onSaved={onSaved} />
      ) : active === 'risk' ? (
        <RiskTab s={s} onSaved={onSaved} />
      ) : active === 'tarama' ? (
        <ScannerTab s={s} onSaved={onSaved} />
      ) : active === 'simulasyon' ? (
        <PaperTab s={s} onSaved={onSaved} />
      ) : (
        <SecurityTab />
      )}
    </div>
  );
}
