import clsx from 'clsx';
import { AlertTriangle, Info, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button } from './primitives';

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const on = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [open, onClose]);
}

function useScrollLock(open: boolean) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = 'max-w-lg',
  icon,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: string;
  icon?: ReactNode;
}) {
  useEscape(open, onClose);
  useScrollLock(open);
  const titleId = useId();
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
      <div className="absolute inset-0 animate-fade-in bg-black/60 backdrop-blur-[2px]" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={clsx('relative w-full animate-scale-in rounded-xl border border-line bg-surface shadow-pop', width)}
      >
        <div className="flex items-start gap-3 px-5 pt-5">
          {icon && <div className="mt-0.5 shrink-0">{icon}</div>}
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-[15px] font-semibold text-fg">
              {title}
            </h2>
            {subtitle && <div className="mt-1 text-[12.5px] leading-relaxed text-muted">{subtitle}</div>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1 -mt-1 rounded-md p-1.5 text-subtle hover:bg-hover hover:text-fg"
            aria-label="Kapat"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {children && <div className="px-5 pb-1 pt-4">{children}</div>}
        {footer && <div className="mt-3 flex items-center justify-end gap-2 border-t border-line px-5 py-3.5">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = 'max-w-[1080px]',
  headerRight,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
  headerRight?: ReactNode;
}) {
  useEscape(open, onClose);
  useScrollLock(open);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[60] flex justify-end">
      <div className="absolute inset-0 animate-fade-in bg-black/55 backdrop-blur-[1px]" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        className={clsx(
          'relative flex h-full w-full animate-slide-in-right flex-col border-l border-line bg-bg shadow-pop',
          width,
        )}
      >
        <header className="flex items-start gap-3 border-b border-line bg-surface px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-semibold text-fg">{title}</div>
            {subtitle && <div className="mt-0.5 text-[12px] text-muted">{subtitle}</div>}
          </div>
          {headerRight}
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-subtle hover:bg-hover hover:text-fg"
            aria-label="Kapat"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer && <footer className="border-t border-line bg-surface px-5 py-3">{footer}</footer>}
      </aside>
    </div>,
    document.body,
  );
}

export interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  tone?: 'danger' | 'primary' | 'success';
  /** Kullanıcının yazması gereken kelime (ör. "CANLI"). */
  typeToConfirm?: string;
  details?: ReactNode;
}

export function ConfirmDialog({
  open,
  options,
  onCancel,
  onConfirm,
  busy,
}: {
  open: boolean;
  options: ConfirmOptions | null;
  onCancel: () => void;
  onConfirm: () => void;
  busy?: boolean;
}) {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (open) setTyped('');
  }, [open]);
  if (!options) return null;
  const tone = options.tone ?? 'primary';
  const needType = !!options.typeToConfirm;
  const ok = !needType || typed.trim().toLocaleUpperCase('tr-TR') === options.typeToConfirm?.toLocaleUpperCase('tr-TR');
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={options.title}
      subtitle={options.message}
      icon={
        tone === 'danger' ? (
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-down/10 text-down">
            <AlertTriangle className="h-[18px] w-[18px]" />
          </div>
        ) : (
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/10 text-accent">
            <Info className="h-[18px] w-[18px]" />
          </div>
        )
      }
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {options.cancelText ?? 'Vazgeç'}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : tone === 'success' ? 'success' : 'primary'}
            onClick={onConfirm}
            disabled={!ok}
            loading={busy}
            autoFocus={!needType}
          >
            {options.confirmText ?? 'Onayla'}
          </Button>
        </>
      }
    >
      {(options.details || needType) && (
        <div className="space-y-3 pb-2">
          {options.details}
          {needType && (
            <label className="block">
              <span className="text-[12px] text-muted">
                Onaylamak için <span className="num font-semibold text-fg">{options.typeToConfirm}</span> yazın
              </span>
              <input
                autoFocus
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && ok) onConfirm();
                }}
                className="num mt-1.5 h-10 w-full rounded-lg border border-line bg-bg px-3 text-[14px] tracking-widest text-fg outline-none focus:border-accent"
              />
            </label>
          )}
        </div>
      )}
    </Modal>
  );
}

/** Onay diyaloğunu promise ile kullanmak için kanca. */
export function useConfirm() {
  const [state, setState] = useState<{ options: ConfirmOptions; resolve: (v: boolean) => void } | null>(null);
  const confirm = (options: ConfirmOptions) =>
    new Promise<boolean>((resolve) => {
      setState({ options, resolve });
    });
  const node = (
    <ConfirmDialog
      open={!!state}
      options={state?.options ?? null}
      onCancel={() => {
        state?.resolve(false);
        setState(null);
      }}
      onConfirm={() => {
        state?.resolve(true);
        setState(null);
      }}
    />
  );
  return { confirm, node };
}

/** Basit açıklama balonu. */
export function Tooltip({
  content,
  children,
  side = 'top',
  className,
  maxWidth = 280,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: 'top' | 'bottom';
  className?: string;
  maxWidth?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setPos({ x: r.left + r.width / 2, y: side === 'top' ? r.top : r.bottom });
  }, [open, side]);
  return (
    <span
      ref={ref}
      className={clsx('inline-flex', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open &&
        pos &&
        createPortal(
          <div
            role="tooltip"
            className="pointer-events-none fixed z-[90] animate-fade-in rounded-lg border border-line-strong bg-elevated px-3 py-2 text-[12px] leading-relaxed text-fg shadow-pop"
            style={{
              left: Math.min(Math.max(pos.x, maxWidth / 2 + 8), window.innerWidth - maxWidth / 2 - 8),
              top: side === 'top' ? pos.y - 8 : pos.y + 8,
              transform: side === 'top' ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
              maxWidth,
            }}
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  );
}

export function InfoTip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Tooltip content={children}>
      <span
        tabIndex={0}
        className={clsx('inline-flex cursor-help text-subtle transition-colors hover:text-muted', className)}
        aria-label="Bilgi"
      >
        <Info className="h-3.5 w-3.5" />
      </span>
    </Tooltip>
  );
}

/** Dışarı tıklayınca kapanan küçük menü. */
export function Popover({
  open,
  onClose,
  anchor,
  children,
  align = 'right',
}: {
  open: boolean;
  onClose: () => void;
  anchor: ReactNode;
  children: ReactNode;
  align?: 'left' | 'right';
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEscape(open, onClose);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener('mousedown', on);
    return () => window.removeEventListener('mousedown', on);
  }, [open, onClose]);
  return (
    <div ref={ref} className="relative">
      {anchor}
      {open && (
        <div
          className={clsx(
            'absolute top-full z-[55] mt-2 min-w-[220px] animate-scale-in rounded-xl border border-line bg-elevated p-1.5 shadow-pop',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  icon,
  children,
  onClick,
  danger,
  disabled,
}: {
  icon?: ReactNode;
  children: ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors disabled:opacity-40',
        danger ? 'text-down hover:bg-down/10' : 'text-fg hover:bg-hover',
      )}
    >
      {icon && <span className={clsx('shrink-0', danger ? 'text-down' : 'text-muted')}>{icon}</span>}
      {children}
    </button>
  );
}
