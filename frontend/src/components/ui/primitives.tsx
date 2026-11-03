import clsx from 'clsx';
import { Loader2 } from 'lucide-react';
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'outline-danger' | 'outline-success';
type Size = 'xs' | 'sm' | 'md' | 'lg';

const variantCls: Record<Variant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent/90 active:bg-accent/80 shadow-[0_1px_0_0_rgba(255,255,255,0.15)_inset,0_1px_2px_rgba(0,0,0,0.4)]',
  secondary: 'bg-elevated text-fg border border-line hover:bg-hover hover:border-line-strong',
  ghost: 'text-muted hover:text-fg hover:bg-hover',
  danger: 'bg-down text-white hover:bg-down/90 active:bg-down/80 shadow-[0_1px_0_0_rgba(255,255,255,0.15)_inset]',
  success: 'bg-up text-[#04140D] hover:bg-up/90 active:bg-up/80 shadow-[0_1px_0_0_rgba(255,255,255,0.2)_inset]',
  'outline-danger': 'border border-down/40 text-down hover:bg-down/10',
  'outline-success': 'border border-up/40 text-up hover:bg-up/10',
};
const sizeCls: Record<Size, string> = {
  xs: 'h-7 px-2 text-[11.5px] gap-1 rounded-md',
  sm: 'h-8 px-3 text-[12.5px] gap-1.5 rounded-md',
  md: 'h-9 px-3.5 text-[13px] gap-2 rounded-lg',
  lg: 'h-11 px-5 text-[14px] gap-2 rounded-lg',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
  block?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, block, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      disabled={disabled || loading}
      className={clsx(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium transition-colors duration-150',
        'disabled:cursor-not-allowed disabled:opacity-50',
        variantCls[variant],
        sizeCls[size],
        block && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  );
});

export function IconButton({
  label,
  className,
  children,
  active,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; active?: boolean }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={clsx(
        'inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-hover hover:text-fg',
        active && 'bg-hover text-fg',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

type Tone = 'neutral' | 'accent' | 'up' | 'down' | 'warn' | 'proj';
const toneCls: Record<Tone, string> = {
  neutral: 'bg-elevated text-muted border-line',
  accent: 'bg-accent/10 text-accent border-accent/25',
  up: 'bg-up/10 text-up border-up/25',
  down: 'bg-down/10 text-down border-down/25',
  warn: 'bg-warn/10 text-warn border-warn/25',
  proj: 'bg-proj/10 text-proj border-proj/25',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
  dot,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span
      className={clsx(
        'inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-md border px-2 text-[11px] font-semibold tracking-wide',
        toneCls[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function SideBadge({ side, size = 'md' }: { side: 'LONG' | 'SHORT'; size?: 'sm' | 'md' }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-md font-bold tracking-wider',
        size === 'sm' ? 'h-[18px] px-1.5 text-[10px]' : 'h-[22px] px-2 text-[11px]',
        side === 'LONG' ? 'text-up ring-1 ring-inset ring-up/25' : 'text-down ring-1 ring-inset ring-down/25',
      )}
      style={{ backgroundColor: side === 'LONG' ? 'rgb(23 201 135 / 0.12)' : 'rgb(240 75 91 / 0.12)' }}
    >
      {side}
    </span>
  );
}

export function Card({
  className,
  children,
  ...rest
}: { className?: string; children: ReactNode } & HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx('card', className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  icon,
  right,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('card-head', className)}>
      <div className="flex min-w-0 items-center gap-2.5">
        {icon && <span className="text-subtle">{icon}</span>}
        <div className="min-w-0">
          <div className="card-title truncate">{title}</div>
          {subtitle && <div className="truncate text-[11.5px] text-subtle">{subtitle}</div>}
        </div>
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  disabled,
  size = 'md',
  tone = 'accent',
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  size?: 'sm' | 'md' | 'lg';
  tone?: 'accent' | 'up';
  label?: string;
}) {
  const dims =
    size === 'lg'
      ? { w: 'w-12', h: 'h-7', knob: 'h-5 w-5', on: 'translate-x-6' }
      : size === 'sm'
        ? { w: 'w-8', h: 'h-[18px]', knob: 'h-3 w-3', on: 'translate-x-[14px]' }
        : { w: 'w-10', h: 'h-6', knob: 'h-4 w-4', on: 'translate-x-4' };
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx(
        'relative inline-flex shrink-0 items-center rounded-full border transition-colors duration-200',
        dims.w,
        dims.h,
        checked
          ? tone === 'up'
            ? 'border-up/60 bg-up'
            : 'border-accent/60 bg-accent'
          : 'border-line-strong bg-elevated',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <span
        className={clsx(
          'ml-[3px] inline-block rounded-full bg-white shadow transition-transform duration-200',
          dims.knob,
          checked ? dims.on : 'translate-x-0',
        )}
      />
    </button>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton', className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={clsx('h-4 w-4 animate-spin text-muted', className)} />;
}

export function Empty({
  icon,
  title,
  children,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('flex flex-col items-center justify-center px-6 py-10 text-center', className)}>
      {icon && (
        <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-elevated text-subtle">
          {icon}
        </div>
      )}
      <div className="text-[13.5px] font-semibold text-fg">{title}</div>
      {children && <div className="mt-1 max-w-sm text-[12.5px] leading-relaxed text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Yatay doluluk göstergesi (0..1), isteğe bağlı limit çizgisi. */
export function Meter({
  value,
  limit,
  tone,
  className,
}: {
  value: number | null | undefined;
  limit?: number | null;
  tone?: 'auto' | 'accent' | 'up' | 'warn' | 'down';
  className?: string;
}) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  const lim = limit != null && limit > 0 ? Math.min(1, limit) : null;
  const ratio = lim ? v / lim : v;
  const t = tone && tone !== 'auto' ? tone : ratio >= 1 ? 'down' : ratio >= 0.75 ? 'warn' : 'accent';
  const color = { accent: 'bg-accent', up: 'bg-up', warn: 'bg-warn', down: 'bg-down' }[t];
  return (
    <div className={clsx('relative h-1.5 w-full overflow-hidden rounded-full bg-elevated', className)}>
      <div className={clsx('h-full rounded-full transition-[width] duration-500', color)} style={{ width: `${v * 100}%` }} />
      {lim != null && (
        <div className="absolute top-[-2px] h-[10px] w-[2px] rounded bg-fg/60" style={{ left: `calc(${lim * 100}% - 1px)` }} />
      )}
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = 'sm',
  className,
}: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
  size?: 'xs' | 'sm';
  className?: string;
}) {
  return (
    <div className={clsx('inline-flex rounded-lg border border-line bg-bg p-0.5', className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={clsx(
            'rounded-md font-medium transition-colors',
            size === 'xs' ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-[12px]',
            o.value === value ? 'bg-elevated text-fg shadow-card ring-1 ring-inset ring-line' : 'text-muted hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function StatusDot({ state, pulse }: { state: 'ok' | 'warn' | 'down' | 'off'; pulse?: boolean }) {
  const c = { ok: 'bg-up', warn: 'bg-warn', down: 'bg-down', off: 'bg-subtle' }[state];
  return (
    <span className="relative inline-flex h-2 w-2">
      {pulse && state !== 'off' && <span className={clsx('absolute inline-flex h-full w-full animate-ping rounded-full opacity-40', c)} />}
      <span className={clsx('relative inline-flex h-2 w-2 rounded-full', c)} />
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-elevated px-1.5 py-0.5 font-mono text-[10.5px] text-muted">{children}</kbd>
  );
}

/** Başlık + değer bloğu */
export function Stat({
  label,
  value,
  sub,
  className,
  valueClassName,
  hint,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  className?: string;
  valueClassName?: string;
  hint?: ReactNode;
}) {
  return (
    <div className={clsx('min-w-0', className)}>
      <div className="label flex items-center gap-1">
        {label}
        {hint}
      </div>
      <div className={clsx('num mt-1 truncate text-[15px] font-medium', valueClassName ?? 'text-fg')}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11.5px] text-muted">{sub}</div>}
    </div>
  );
}
