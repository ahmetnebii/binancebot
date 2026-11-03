import clsx from 'clsx';
import { X } from 'lucide-react';
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react';
import { forwardRef, useEffect, useState } from 'react';

export function Field({
  label,
  hint,
  error,
  children,
  className,
  right,
  htmlFor,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  right?: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className={clsx('min-w-0', className)}>
      {(label || right) && (
        <div className="mb-1.5 flex items-center justify-between gap-2">
          {label && (
            <label htmlFor={htmlFor} className="flex items-center gap-1 text-[12px] font-medium text-muted">
              {label}
            </label>
          )}
          {right}
        </div>
      )}
      {children}
      {error ? (
        <div className="mt-1 text-[11.5px] text-down">{error}</div>
      ) : hint ? (
        <div className="mt-1 text-[11.5px] leading-relaxed text-subtle">{hint}</div>
      ) : null}
    </div>
  );
}

const inputBase =
  'h-9 w-full rounded-lg border border-line bg-bg px-3 text-[13px] text-fg outline-none transition-colors placeholder:text-subtle hover:border-line-strong focus:border-accent focus:ring-2 focus:ring-accent/20 disabled:opacity-50';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }>(
  function Input({ className, mono, ...rest }, ref) {
    return <input ref={ref} className={clsx(inputBase, mono && 'num', className)} {...rest} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={clsx(inputBase, 'h-auto min-h-[96px] py-2 font-mono text-[12px] leading-relaxed', className)}
        {...rest}
      />
    );
  },
);

/**
 * Sayı girişi. Türkçe ondalık virgülü kabul eder.
 * scale: gösterim çarpanı (ör. yüzde için 100: 0.0075 → "0,75").
 */
export function NumberInput({
  value,
  onChange,
  suffix,
  prefix,
  scale = 1,
  min,
  max,
  step,
  decimals,
  placeholder,
  disabled,
  className,
  allowEmpty,
  id,
}: {
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  suffix?: ReactNode;
  prefix?: ReactNode;
  scale?: number;
  min?: number;
  max?: number;
  step?: number;
  decimals?: number;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  allowEmpty?: boolean;
  id?: string;
}) {
  const toText = (v: number | null | undefined) => {
    if (v === null || v === undefined || !Number.isFinite(v)) return '';
    const shown = v * scale;
    const d = decimals ?? (Math.abs(shown) >= 100 ? 2 : Math.abs(shown) >= 1 ? 4 : 8);
    return String(Number(shown.toFixed(d))).replace('.', ',');
  };
  const [text, setText] = useState(() => toText(value));
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setText(toText(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, focused]);

  const normalize = (t: string) => {
    let s = t.replace(/\s/g, '');
    // "1.234,5" → binlik noktaları kaldır, virgülü ondalığa çevir; "0.5" olduğu gibi kalır
    if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.');
    return s;
  };

  const commit = (t: string) => {
    const norm = normalize(t);
    if (norm === '' || norm === '-') {
      if (allowEmpty) onChange(null);
      return;
    }
    const n = Number(norm);
    if (!Number.isFinite(n)) return;
    let v = n / scale;
    if (min !== undefined) v = Math.max(min, v);
    if (max !== undefined) v = Math.min(max, v);
    onChange(v);
  };

  return (
    <div
      className={clsx(
        'flex h-9 w-full items-center rounded-lg border border-line bg-bg transition-colors hover:border-line-strong focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20',
        disabled && 'opacity-50',
        className,
      )}
    >
      {prefix && <span className="pl-3 text-[12px] text-subtle">{prefix}</span>}
      <input
        id={id}
        inputMode="decimal"
        disabled={disabled}
        value={text}
        placeholder={placeholder}
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          commit(text);
        }}
        onChange={(e) => {
          setText(e.target.value);
          // Yazarken anlık güncelle (geçerli sayıysa)
          const t = normalize(e.target.value);
          const n = Number(t);
          if (t !== '' && Number.isFinite(n)) {
            let v = n / scale;
            if (min !== undefined && v < min) return;
            if (max !== undefined && v > max) return;
            onChange(v);
          } else if (t === '' && allowEmpty) onChange(null);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && step) {
            e.preventDefault();
            const cur = value ?? 0;
            let v = cur + (e.key === 'ArrowUp' ? step : -step);
            if (min !== undefined) v = Math.max(min, v);
            if (max !== undefined) v = Math.min(max, v);
            onChange(Number(v.toFixed(10)));
          }
        }}
        className="num h-full w-full min-w-0 bg-transparent px-3 text-[13px] text-fg outline-none placeholder:text-subtle"
      />
      {suffix && <span className="pr-3 text-[12px] text-subtle">{suffix}</span>}
    </div>
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
  className,
  disabled,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  className?: string;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as T)}
      className={clsx(inputBase, 'cursor-pointer appearance-none pr-8', className)}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238B95A5' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E\")",
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'right 10px center',
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value} className="bg-elevated">
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Slider({
  value,
  onChange,
  min,
  max,
  step,
  className,
  tone = 'accent',
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  className?: string;
  tone?: 'accent' | 'warn' | 'down';
}) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
  const color = tone === 'down' ? '#F04B5B' : tone === 'warn' ? '#F5A524' : '#5B8CFF';
  return (
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className={clsx('bb-range h-5 w-full cursor-pointer appearance-none bg-transparent', className)}
      style={{
        // iz rengi
        ['--bb-fill' as string]: color,
        ['--bb-pct' as string]: `${pct}%`,
      }}
    />
  );
}

export function ChipsInput({
  value,
  onChange,
  placeholder,
  transform = (s) => s.trim().toUpperCase(),
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  transform?: (s: string) => string;
}) {
  const [text, setText] = useState('');
  const add = () => {
    const parts = text
      .split(/[\s,;]+/)
      .map(transform)
      .filter(Boolean);
    if (!parts.length) return;
    const next = Array.from(new Set([...value, ...parts]));
    onChange(next);
    setText('');
  };
  return (
    <div className="flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-lg border border-line bg-bg px-2 py-1.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20">
      {value.map((v) => (
        <span key={v} className="num inline-flex items-center gap-1 rounded-md border border-line bg-elevated px-2 py-0.5 text-[11.5px] text-fg">
          {v}
          <button
            type="button"
            aria-label={`${v} kaldır`}
            onClick={() => onChange(value.filter((x) => x !== v))}
            className="text-subtle hover:text-down"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          } else if (e.key === 'Backspace' && !text && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={add}
        placeholder={value.length ? '' : placeholder}
        className="num h-6 min-w-[120px] flex-1 bg-transparent px-1 text-[12.5px] text-fg outline-none placeholder:text-subtle"
      />
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={clsx('flex cursor-pointer items-start justify-between gap-4 py-1', disabled && 'cursor-not-allowed opacity-60')}>
      <span className="min-w-0">
        <span className="block text-[13px] font-medium text-fg">{label}</span>
        {description && <span className="mt-0.5 block text-[12px] leading-relaxed text-muted">{description}</span>}
      </span>
      <span className="pt-0.5">
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          disabled={disabled}
          onClick={() => onChange(!checked)}
          className={clsx(
            'relative inline-flex h-6 w-10 shrink-0 items-center rounded-full border transition-colors',
            checked ? 'border-accent/60 bg-accent' : 'border-line-strong bg-elevated',
          )}
        >
          <span
            className={clsx(
              'ml-[3px] inline-block h-4 w-4 rounded-full bg-white shadow transition-transform',
              checked ? 'translate-x-4' : 'translate-x-0',
            )}
          />
        </button>
      </span>
    </label>
  );
}
