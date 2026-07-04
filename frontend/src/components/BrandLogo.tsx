import { useId } from 'react';

type BrandLogoProps = {
  variant?: 'icon' | 'full';
  size?: number;
  subtitle?: string;
  dark?: boolean;
  className?: string;
};

function BrandIcon({ size = 32 }: { size?: number }) {
  const outerGradientId = useId();
  const accentGradientId = useId();

  return (
    <svg
      className="brand-logo-icon"
      width={size}
      height={size}
      viewBox="0 0 120 120"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={outerGradientId} x1="16" y1="96" x2="100" y2="24" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1D4ED8" />
          <stop offset="0.55" stopColor="#2563EB" />
          <stop offset="1" stopColor="#22D3EE" />
        </linearGradient>
        <linearGradient id={accentGradientId} x1="38" y1="88" x2="96" y2="42" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2563EB" />
          <stop offset="1" stopColor="#2DD4BF" />
        </linearGradient>
      </defs>

      <path
        d="M85 29C77 21 66 17 54 17c-24.85 0-45 20.15-45 45s20.15 45 45 45c11.34 0 21.71-4.2 29.63-11.14"
        stroke={`url(#${outerGradientId})`}
        strokeWidth="12"
        strokeLinecap="round"
      />
      <path
        d="M53 36c16.42 0 31.07 7.57 40.67 19.42L72 66.67"
        stroke={`url(#${accentGradientId})`}
        strokeWidth="10"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M72 66.67 94 91"
        stroke={`url(#${accentGradientId})`}
        strokeWidth="10"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      <circle cx="54" cy="60" r="22" stroke="#60A5FA" strokeWidth="2.5" opacity="0.85" />
      <path d="M32 54c7-10 18.64-16 31-16 9.04 0 17.69 3.22 24.46 9.1" stroke="#38BDF8" strokeWidth="2.5" strokeLinecap="round" opacity="0.92" />
      <path d="M34 74c8 5.68 17.57 8.72 27.38 8.72 4.66 0 9.27-.69 13.68-2.07" stroke="#2563EB" strokeWidth="2.5" strokeLinecap="round" opacity="0.85" />

      <circle cx="34" cy="54" r="5.5" fill="#3B82F6" />
      <circle cx="48" cy="36" r="5.5" fill="#14B8A6" />
      <circle cx="85" cy="47" r="5.5" fill="#38BDF8" />

      <path d="M60 50c5.52 0 10 4.48 10 10v6c0 5.52-4.48 10-10 10s-10-4.48-10-10v-6c0-5.52 4.48-10 10-10Z" stroke="#1D4ED8" strokeWidth="2.8" />
      <path d="M56 46.5 60 50l4-3.5" stroke="#1D4ED8" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M50 59h-6m6 8h-6m32-8h-6m6 8h-6M55 54l-4-4m18 4 4-4" stroke="#1D4ED8" strokeWidth="2.6" strokeLinecap="round" />
      <path d="M54 56v14m12-14v14" stroke="#1D4ED8" strokeWidth="2.2" strokeLinecap="round" opacity="0.6" />
    </svg>
  );
}

export function BrandLogo({
  variant = 'full',
  size = 32,
  subtitle,
  dark = false,
  className = '',
}: BrandLogoProps) {
  const rootClassName = [
    'brand-logo',
    variant === 'icon' ? 'is-icon-only' : '',
    dark ? 'is-dark' : '',
    className,
  ].filter(Boolean).join(' ');

  if (variant === 'icon') {
    return (
      <span className={rootClassName} aria-label="QualiBug AI">
        <BrandIcon size={size} />
      </span>
    );
  }

  return (
    <div className={rootClassName} aria-label="QualiBug AI">
      <BrandIcon size={size} />
      <span className="brand-logo-copy">
        <span className="brand-logo-title">
          <span className="brand-logo-name">QualiBug</span>
          <span className="brand-logo-ai">AI</span>
        </span>
        {subtitle ? <span className="brand-logo-subtitle">{subtitle}</span> : null}
      </span>
    </div>
  );
}
