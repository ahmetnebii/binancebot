import clsx from 'clsx';
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { createPortal } from 'react-dom';
import { useApp } from '../../store/app';

const ICON = {
  success: <CheckCircle2 className="h-[18px] w-[18px] text-up" />,
  info: <Info className="h-[18px] w-[18px] text-accent" />,
  warning: <AlertTriangle className="h-[18px] w-[18px] text-warn" />,
  error: <XCircle className="h-[18px] w-[18px] text-down" />,
};
const BAR = { success: 'bg-up', info: 'bg-accent', warning: 'bg-warn', error: 'bg-down' };

export function Toaster() {
  const toasts = useApp((s) => s.toasts);
  const dismiss = useApp((s) => s.dismissToast);
  return createPortal(
    <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="pointer-events-auto relative flex animate-slide-in-right items-start gap-3 overflow-hidden rounded-xl border border-line-strong bg-elevated py-3 pl-4 pr-3 shadow-pop"
        >
          <span className={clsx('absolute inset-y-0 left-0 w-[3px]', BAR[t.level])} />
          <span className="mt-px shrink-0">{ICON[t.level]}</span>
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold text-fg">{t.title}</div>
            {t.message && <div className="mt-0.5 break-words text-[12px] leading-relaxed text-muted">{t.message}</div>}
          </div>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            className="shrink-0 rounded-md p-1 text-subtle hover:bg-hover hover:text-fg"
            aria-label="Kapat"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}
