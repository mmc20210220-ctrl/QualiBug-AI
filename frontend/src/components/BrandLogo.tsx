import {
  BehaviorFieldMark,
  type BrandDetail,
  type BrandTone,
} from '../brand/BehaviorFieldMark';

type BrandLogoProps = {
  variant?: 'icon' | 'full';
  detail: BrandDetail;
  tone: BrandTone;
  size?: number;
  subtitle?: string;
  className?: string;
};

export function BrandLogo({
  variant = 'full',
  detail,
  tone,
  size = 32,
  subtitle,
  className = '',
}: BrandLogoProps) {
  const darkSurface = tone === 'dark' || tone === 'mono-light';
  const rootClassName = [
    'brand-logo',
    variant === 'icon' ? 'is-icon-only' : '',
    darkSurface ? 'is-dark' : '',
    className,
  ].filter(Boolean).join(' ');
  const mark = (
    <BehaviorFieldMark
      detail={detail}
      tone={tone}
      size={size}
      className="brand-logo-icon"
    />
  );

  if (variant === 'icon') {
    return (
      <span className={rootClassName} aria-label="QualiBug AI">
        {mark}
      </span>
    );
  }

  return (
    <div className={rootClassName} aria-label="QualiBug AI">
      {mark}
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
