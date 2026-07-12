import type { BrandDetail, BrandTone } from './BehaviorFieldMark';
import source from './behavior-field-brand.json';

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

type RenderBehaviorFieldSvgOptions = {
  detail: BrandDetail;
  tone: BrandTone;
  width: number;
  height: number;
  idPrefix: string;
  ariaHidden?: boolean;
};

const variants = source.variants as unknown as Record<BrandDetail, VariantSpec>;
const palettes = source.palettes as unknown as Record<BrandTone, Palette>;

export function renderBehaviorFieldSvg({
  detail,
  tone,
  width,
  height,
  idPrefix,
  ariaHidden = false,
}: RenderBehaviorFieldSvgOptions): string {
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
    throw new Error('Behavior Field SVG dimensions must be positive finite numbers');
  }
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(idPrefix)) {
    throw new Error(`Behavior Field SVG idPrefix is invalid: ${idPrefix}`);
  }

  const variant = variants[detail];
  const palette = palettes[tone];
  const qStrokeWidth = detail === 'master' ? 13 : detail === 'compact' ? 11 : 9;
  const plane = variant.planePath
    ? `<path d="${variant.planePath}" fill="${palette.planeFill}" stroke="${palette.planeStroke}" stroke-width="${detail === 'master' ? 2.5 : 3}" stroke-linejoin="round"/>`
    : '';
  const grid = variant.gridPaths
    .map((path) => `<path d="${path}" fill="none" stroke="${palette.grid}" stroke-width="1.5" opacity=".55"/>`)
    .join('');
  const nodes = variant.nodes
    .map((node, index) => `<circle cx="${node.cx}" cy="${node.cy}" r="${node.r}" fill="${palette.nodes[index] ?? palette.nodes[0]}"/>`)
    .join('');
  const accessibility = ariaHidden
    ? 'aria-hidden="true"'
    : 'role="img" aria-label="QualiBug AI"';

  return `<svg width="${width}" height="${height}" viewBox="${variant.viewBox}" fill="none" xmlns="http://www.w3.org/2000/svg" ${accessibility}><defs><linearGradient id="${idPrefix}-q" x1="12%" y1="90%" x2="90%" y2="10%"><stop offset="0" stop-color="${palette.outer[0]}"/><stop offset=".55" stop-color="${palette.outer[1]}"/><stop offset="1" stop-color="${palette.outer[2]}"/></linearGradient></defs><path d="${variant.qPath}" fill="none" stroke="url(#${idPrefix}-q)" stroke-width="${qStrokeWidth}" stroke-linecap="round"/>${plane}${grid}<path d="${variant.trajectoryPath}" fill="none" stroke="${palette.trajectory}" stroke-width="${detail === 'micro' ? 4 : 3.5}" stroke-linecap="round"/>${nodes}</svg>`;
}
