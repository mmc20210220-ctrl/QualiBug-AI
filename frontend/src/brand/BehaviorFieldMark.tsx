import { useId } from 'react';
import source from './behavior-field-brand.json';

export type BrandDetail = 'master' | 'compact' | 'micro';
export type BrandTone = 'dark' | 'light' | 'mono-dark' | 'mono-light';

type NodeSpec = {
  cx: number;
  cy: number;
  r: number;
};

type VariantSpec = {
  viewBox: string;
  qPath: string;
  planePath: string | null;
  gridPaths: string[];
  trajectoryPath: string;
  nodes: NodeSpec[];
};

type Palette = {
  outer: [string, string, string];
  planeFill: string;
  planeStroke: string;
  grid: string;
  trajectory: string;
  nodes: string[];
};

const variants = source.variants as unknown as Record<BrandDetail, VariantSpec>;
const palettes = source.palettes as unknown as Record<BrandTone, Palette>;

type BehaviorFieldMarkProps = {
  detail: BrandDetail;
  tone: BrandTone;
  size: number;
  className?: string;
};

export function BehaviorFieldMark({ detail, tone, size, className = '' }: BehaviorFieldMarkProps) {
  const gradientId = useId();
  const variant = variants[detail];
  const palette = palettes[tone];
  const qStrokeWidth = detail === 'master' ? 13 : detail === 'compact' ? 11 : 9;

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox={variant.viewBox}
      aria-hidden="true"
      data-brand-detail={detail}
      data-brand-tone={tone}
    >
      <defs>
        <linearGradient id={gradientId} x1="12%" y1="90%" x2="90%" y2="10%">
          <stop offset="0" stopColor={palette.outer[0]} />
          <stop offset="0.55" stopColor={palette.outer[1]} />
          <stop offset="1" stopColor={palette.outer[2]} />
        </linearGradient>
      </defs>
      <path
        d={variant.qPath}
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth={qStrokeWidth}
        strokeLinecap="round"
      />
      {variant.planePath ? (
        <path
          d={variant.planePath}
          fill={palette.planeFill}
          stroke={palette.planeStroke}
          strokeWidth={detail === 'master' ? 2.5 : 3}
          strokeLinejoin="round"
        />
      ) : null}
      {variant.gridPaths.map((path) => (
        <path
          key={path}
          d={path}
          fill="none"
          stroke={palette.grid}
          strokeWidth="1.5"
          opacity="0.55"
        />
      ))}
      <path
        d={variant.trajectoryPath}
        fill="none"
        stroke={palette.trajectory}
        strokeWidth={detail === 'micro' ? 4 : 3.5}
        strokeLinecap="round"
      />
      {variant.nodes.map((node, index) => (
        <circle
          key={`${node.cx}-${node.cy}`}
          cx={node.cx}
          cy={node.cy}
          r={node.r}
          fill={palette.nodes[index] ?? palette.nodes[0]}
        />
      ))}
    </svg>
  );
}
