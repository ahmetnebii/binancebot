/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'rgb(var(--c-bg) / <alpha-value>)',
        surface: 'rgb(var(--c-surface) / <alpha-value>)',
        elevated: 'rgb(var(--c-elevated) / <alpha-value>)',
        hover: 'rgb(var(--c-hover) / <alpha-value>)',
        line: 'rgb(var(--c-border) / <alpha-value>)',
        'line-strong': 'rgb(var(--c-border-strong) / <alpha-value>)',
        fg: 'rgb(var(--c-text) / <alpha-value>)',
        muted: 'rgb(var(--c-muted) / <alpha-value>)',
        subtle: 'rgb(var(--c-subtle) / <alpha-value>)',
        accent: 'rgb(var(--c-accent) / <alpha-value>)',
        up: 'rgb(var(--c-up) / <alpha-value>)',
        down: 'rgb(var(--c-down) / <alpha-value>)',
        warn: 'rgb(var(--c-warn) / <alpha-value>)',
        proj: 'rgb(var(--c-proj) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['10.5px', { lineHeight: '14px' }],
      },
      boxShadow: {
        card: '0 1px 0 0 rgba(255,255,255,0.02) inset, 0 1px 2px rgba(0,0,0,0.35)',
        pop: '0 0 0 1px rgba(255,255,255,0.04), 0 12px 32px -8px rgba(0,0,0,0.6), 0 4px 12px -4px rgba(0,0,0,0.4)',
        glow: '0 0 0 1px rgba(91,140,255,0.35), 0 0 24px -4px rgba(91,140,255,0.45)',
      },
      borderRadius: {
        xl: '12px',
        lg: '10px',
        md: '8px',
      },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'slide-in-right': { from: { transform: 'translateX(24px)', opacity: '0' }, to: { transform: 'translateX(0)', opacity: '1' } },
        'scale-in': { from: { transform: 'scale(0.97)', opacity: '0' }, to: { transform: 'scale(1)', opacity: '1' } },
        'live-pulse': { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        flash: { '0%': { backgroundColor: 'rgba(91,140,255,0.18)' }, '100%': { backgroundColor: 'transparent' } },
      },
      animation: {
        'fade-in': 'fade-in 160ms ease-out',
        'slide-in-right': 'slide-in-right 220ms cubic-bezier(0.2, 0.8, 0.2, 1)',
        'scale-in': 'scale-in 160ms cubic-bezier(0.2, 0.8, 0.2, 1)',
        'live-pulse': 'live-pulse 1.8s ease-in-out infinite',
        flash: 'flash 1.2s ease-out',
      },
    },
  },
  plugins: [],
};
