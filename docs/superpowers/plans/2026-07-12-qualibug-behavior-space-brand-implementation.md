# QualiBug Behavior-Space Brand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. The user explicitly prohibited subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the insect/radar brand language with a governed Behavior Field identity while preserving every current visible product phrase.

**Architecture:** A JSON brand manifest is the geometry and palette source of truth. A typed React mark consumes it at runtime, while a dependency-free Node exporter produces checked SVG and ICO assets from the same manifest. The login canvas becomes a deterministic behavior-space renderer whose visual state is observable and completely separate from real service health.

**Tech Stack:** React 19, TypeScript 5, Vite 8, HTML Canvas 2D, Node.js built-ins, Playwright.

## Global Constraints

- Frontend remains on port `5174`; backend remains on port `8088`.
- Preserve all current visible login, registration, reset, sidebar, navigation, health, and product copy exactly.
- `QualiBug AI` means enterprise software behavior-space infrastructure; the implementation stays industry-neutral.
- No insect body, head, antenna, leg, shell, crawler, spider web, scraping path, radar ring, rotating scan wedge, scanning beam, random particle mesh, or crawler wording.
- Brand visuals never imply API, provider, campaign, model, scan, coverage, or evaluator health.
- No benchmark, customer, industry, hidden-ground-truth, or runtime-finding data enters brand geometry or animation.
- Add no runtime or development dependency.
- Missing assets, invalid variants, malformed SVG/ICO, and canvas failures remain observable and fail the appropriate check.
- Preserve unrelated dirty-worktree changes. Stage and commit only files named by the active task.
- Do not use subagents.
- After each TypeScript or TSX edit, run `npm run typecheck`; after each script edit, run its focused Node contract; after generated asset edits, run `npm run brand:check`.

---

## File Structure

### Create

- `frontend/src/brand/behavior-field-brand.json` — canonical geometry, palettes, and variant metadata.
- `frontend/src/brand/BehaviorFieldMark.tsx` — typed SVG renderer for master, compact, and micro marks.
- `frontend/src/brand/behaviorFieldCanvas.ts` — deterministic behavior-space Canvas 2D renderer.
- `frontend/scripts/brand/brand-mark-contract.mjs` — source-level mark, call-site, and no-insect contract.
- `frontend/scripts/brand/export-assets.mjs` — dependency-free SVG/ICO exporter and drift checker.
- `frontend/scripts/brand/behavior-space-canvas-contract.mjs` — source-level no-radar and observable-state contract.
- `frontend/public/brand/*.svg` — generated official mark variants.
- `frontend/public/favicon.svg` — generated micro SVG.
- `frontend/public/favicon.ico` — generated 32px ICO containing a PNG image.

### Modify

- `frontend/src/components/BrandLogo.tsx` — consume `BehaviorFieldMark`; expose explicit detail and tone props.
- `frontend/src/components/LoginStageCanvas.tsx` — own lifecycle, reduced motion, state, and error reporting around the pure renderer.
- `frontend/src/pages/Login.tsx` — select compact dark mark and remove the scan-sweep decoration without changing copy.
- `frontend/src/components/Sidebar.tsx` — select compact dark mark without changing subtitle or navigation copy.
- `frontend/src/index.css` — remove scan-sweep rules; preserve layout and current text styling.
- `frontend/scripts/login-page-contract.mjs` — freeze current copy and verify mark, favicon, reduced motion, and failed canvas state.
- `frontend/package.json` — expose focused contracts plus `brand:export`, `brand:check`, and `prebuild`.
- `AGENTS.md` — add the Brand Direction Contract.

---

### Task 1: Governed Behavior Field React Mark

**Files:**
- Create: `frontend/scripts/brand/brand-mark-contract.mjs`
- Create: `frontend/src/brand/behavior-field-brand.json`
- Create: `frontend/src/brand/BehaviorFieldMark.tsx`
- Modify: `frontend/src/components/BrandLogo.tsx`
- Modify: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: `BrandDetail = 'master' | 'compact' | 'micro'`.
- Produces: `BrandTone = 'dark' | 'light' | 'mono-dark' | 'mono-light'`.
- Produces: `BehaviorFieldMark({ detail, tone, size, className? })`.
- Produces: `BrandLogo({ detail, tone, variant, size, subtitle?, className? })`.

- [ ] **Step 1: Write the failing mark contract**

Create `frontend/scripts/brand/brand-mark-contract.mjs`:

```js
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');

function read(path) {
  return readFileSync(resolve(root, path), 'utf8');
}

function requireText(source, token, context) {
  if (!source.includes(token)) throw new Error(`${context}: missing ${JSON.stringify(token)}`);
}

const mark = read('src/brand/BehaviorFieldMark.tsx');
const logo = read('src/components/BrandLogo.tsx');
const login = read('src/pages/Login.tsx');
const sidebar = read('src/components/Sidebar.tsx');

for (const detail of ["'master'", "'compact'", "'micro'"]) requireText(mark, detail, 'brand detail');
for (const tone of ["'dark'", "'light'", "'mono-dark'", "'mono-light'"]) requireText(mark, tone, 'brand tone');
requireText(mark, 'data-brand-detail={detail}', 'mark observability');
requireText(mark, 'data-brand-tone={tone}', 'mark observability');
requireText(logo, '<BehaviorFieldMark', 'BrandLogo source');
requireText(login, 'detail="compact"', 'login mark detail');
requireText(login, 'tone="dark"', 'login mark tone');
requireText(sidebar, 'detail="compact"', 'sidebar mark detail');
requireText(sidebar, 'tone="dark"', 'sidebar mark tone');

for (const forbidden of ['M60 50c5.52', 'm43 30-5-6', 'antenna', 'insect']) {
  if (mark.includes(forbidden) || logo.includes(forbidden)) {
    throw new Error(`Literal-insect brand geometry remains: ${forbidden}`);
  }
}

console.log('PASS brand-mark-contract');
```

Add to `frontend/package.json`:

```json
"test:brand-mark": "node scripts/brand/brand-mark-contract.mjs"
```

- [ ] **Step 2: Run the contract and verify it fails**

Run from `frontend`:

```powershell
npm run test:brand-mark
```

Expected: FAIL because `src/brand/BehaviorFieldMark.tsx` does not exist.

- [ ] **Step 3: Add the canonical manifest**

Create `frontend/src/brand/behavior-field-brand.json` with this exact schema and geometry:

```json
{
  "schemaVersion": "qualibug.behavior-field-brand.v1",
  "palettes": {
    "dark": { "outer": ["#2563EB", "#0EA5E9", "#2DD4BF"], "planeFill": "rgba(14,165,233,0.11)", "planeStroke": "#38BDF8", "grid": "#38BDF8", "trajectory": "#7DD3FC", "nodes": ["#3B82F6", "#22D3EE", "#2DD4BF", "#38BDF8"] },
    "light": { "outer": ["#1D4ED8", "#0EA5E9", "#14B8A6"], "planeFill": "#E0F2FE", "planeStroke": "#0284C7", "grid": "#38BDF8", "trajectory": "#2563EB", "nodes": ["#2563EB", "#0EA5E9", "#14B8A6", "#0284C7"] },
    "mono-dark": { "outer": ["#0F2741", "#0F2741", "#0F2741"], "planeFill": "none", "planeStroke": "#0F2741", "grid": "#0F2741", "trajectory": "#0F2741", "nodes": ["#0F2741", "#0F2741", "#0F2741", "#0F2741"] },
    "mono-light": { "outer": ["#F8FAFC", "#F8FAFC", "#F8FAFC"], "planeFill": "none", "planeStroke": "#F8FAFC", "grid": "#F8FAFC", "trajectory": "#F8FAFC", "nodes": ["#F8FAFC", "#F8FAFC", "#F8FAFC", "#F8FAFC"] }
  },
  "variants": {
    "master": {
      "viewBox": "0 0 120 120",
      "qPath": "M88 25A43 43 0 1 0 87 95M78 82l26 26",
      "planePath": "M27 64 58 44 92 62 61 83 27 64Z",
      "gridPaths": ["M39 57 73 76", "M49 50 83 69", "M42 74 73 53", "M54 81 85 60"],
      "trajectoryPath": "M31 66c11-2 17 2 23-4 7-7 10-14 21-11 7 2 9 7 15 8",
      "nodes": [{ "cx": 31, "cy": 66, "r": 4.5 }, { "cx": 54, "cy": 62, "r": 4.5 }, { "cx": 75, "cy": 51, "r": 4.5 }, { "cx": 90, "cy": 59, "r": 4.5 }]
    },
    "compact": {
      "viewBox": "0 0 96 96",
      "qPath": "M70 20A34 34 0 1 0 69 75M61 65l21 21",
      "planePath": "M26 54 48 40 72 53 50 68 26 54Z",
      "gridPaths": [],
      "trajectoryPath": "M29 55c9-1 13 2 19-4 6-6 12-5 21 1",
      "nodes": [{ "cx": 29, "cy": 55, "r": 4 }, { "cx": 48, "cy": 51, "r": 4 }, { "cx": 69, "cy": 52, "r": 4 }]
    },
    "micro": {
      "viewBox": "0 0 64 64",
      "qPath": "M46 14A23 23 0 1 0 46 50M41 43l14 14",
      "planePath": null,
      "gridPaths": [],
      "trajectoryPath": "M20 35c8-8 16-7 24-2",
      "nodes": [{ "cx": 20, "cy": 35, "r": 4 }, { "cx": 44, "cy": 33, "r": 4 }]
    }
  }
}
```

- [ ] **Step 4: Implement the typed mark renderer**

Create `frontend/src/brand/BehaviorFieldMark.tsx`:

```tsx
import { useId } from 'react';
import source from './behavior-field-brand.json';

export type BrandDetail = 'master' | 'compact' | 'micro';
export type BrandTone = 'dark' | 'light' | 'mono-dark' | 'mono-light';

type NodeSpec = { cx: number; cy: number; r: number };
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
      <path d={variant.qPath} fill="none" stroke={`url(#${gradientId})`} strokeWidth={detail === 'master' ? 13 : detail === 'compact' ? 11 : 9} strokeLinecap="round" />
      {variant.planePath ? <path d={variant.planePath} fill={palette.planeFill} stroke={palette.planeStroke} strokeWidth={detail === 'master' ? 2.5 : 3} strokeLinejoin="round" /> : null}
      {variant.gridPaths.map((path) => <path key={path} d={path} fill="none" stroke={palette.grid} strokeWidth="1.5" opacity="0.55" />)}
      <path d={variant.trajectoryPath} fill="none" stroke={palette.trajectory} strokeWidth={detail === 'micro' ? 4 : 3.5} strokeLinecap="round" />
      {variant.nodes.map((node, index) => <circle key={`${node.cx}-${node.cy}`} cx={node.cx} cy={node.cy} r={node.r} fill={palette.nodes[index] ?? palette.nodes[0]} />)}
    </svg>
  );
}
```

- [ ] **Step 5: Make `BrandLogo` use the governed mark and explicit variants**

Replace the internal icon geometry in `frontend/src/components/BrandLogo.tsx` with:

```tsx
import { BehaviorFieldMark, type BrandDetail, type BrandTone } from '../brand/BehaviorFieldMark';

type BrandLogoProps = {
  variant?: 'icon' | 'full';
  detail: BrandDetail;
  tone: BrandTone;
  size?: number;
  subtitle?: string;
  className?: string;
};

export function BrandLogo({ variant = 'full', detail, tone, size = 32, subtitle, className = '' }: BrandLogoProps) {
  const darkSurface = tone === 'dark' || tone === 'mono-light';
  const rootClassName = ['brand-logo', variant === 'icon' ? 'is-icon-only' : '', darkSurface ? 'is-dark' : '', className].filter(Boolean).join(' ');
  const mark = <BehaviorFieldMark detail={detail} tone={tone} size={size} className="brand-logo-icon" />;

  if (variant === 'icon') return <span className={rootClassName} aria-label="QualiBug AI">{mark}</span>;

  return (
    <div className={rootClassName} aria-label="QualiBug AI">
      {mark}
      <span className="brand-logo-copy">
        <span className="brand-logo-title"><span className="brand-logo-name">QualiBug</span><span className="brand-logo-ai">AI</span></span>
        {subtitle ? <span className="brand-logo-subtitle">{subtitle}</span> : null}
      </span>
    </div>
  );
}
```

Update call sites without changing any subtitle or copy:

```tsx
<BrandLogo variant="full" detail="compact" tone="dark" size={40} />
```

```tsx
<BrandLogo variant="full" detail="compact" tone="dark" size={38} subtitle="客户成果台" />
```

- [ ] **Step 6: Run focused verification**

Run from `frontend`:

```powershell
npm run test:brand-mark
npm run typecheck
npm run lint
```

Expected: all commands exit `0`; `test:brand-mark` prints `PASS brand-mark-contract`.

- [ ] **Step 7: Commit Task 1**

```powershell
git add frontend/src/brand/behavior-field-brand.json frontend/src/brand/BehaviorFieldMark.tsx frontend/src/components/BrandLogo.tsx frontend/src/pages/Login.tsx frontend/src/components/Sidebar.tsx frontend/scripts/brand/brand-mark-contract.mjs frontend/package.json
git commit -m "feat: establish behavior-field brand mark"
```

---

### Task 2: Generated SVG and ICO Assets

**Files:**
- Create: `frontend/scripts/brand/export-assets.mjs`
- Create: `frontend/public/brand/*.svg`
- Create: `frontend/public/favicon.svg`
- Create: `frontend/public/favicon.ico`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: `behavior-field-brand.json` from Task 1.
- Produces: `npm run brand:export` and `npm run brand:check`.
- Produces: twelve governed SVG variants plus `favicon.svg` and a 32px ICO.

- [ ] **Step 1: Add export/check scripts before assets exist**

Add these scripts to `frontend/package.json`:

```json
"brand:export": "node scripts/brand/export-assets.mjs",
"brand:check": "node scripts/brand/export-assets.mjs --check",
"prebuild": "npm run brand:check"
```

- [ ] **Step 2: Implement deterministic asset export**

Create `frontend/scripts/brand/export-assets.mjs` with these required functions and behavior:

```js
import { deflateSync } from 'node:zlib';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const source = JSON.parse(readFileSync(resolve(root, 'src/brand/behavior-field-brand.json'), 'utf8'));
const checkOnly = process.argv.includes('--check');

function svg(detail, tone) {
  const variant = source.variants[detail];
  const palette = source.palettes[tone];
  const strokeWidth = detail === 'master' ? 13 : detail === 'compact' ? 11 : 9;
  const plane = variant.planePath ? `<path d="${variant.planePath}" fill="${palette.planeFill}" stroke="${palette.planeStroke}" stroke-width="${detail === 'master' ? 2.5 : 3}" stroke-linejoin="round"/>` : '';
  const grid = variant.gridPaths.map((d) => `<path d="${d}" fill="none" stroke="${palette.grid}" stroke-width="1.5" opacity=".55"/>`).join('');
  const nodes = variant.nodes.map((node, index) => `<circle cx="${node.cx}" cy="${node.cy}" r="${node.r}" fill="${palette.nodes[index] ?? palette.nodes[0]}"/>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${variant.viewBox}" role="img" aria-label="QualiBug AI"><defs><linearGradient id="q" x1="12%" y1="90%" x2="90%" y2="10%"><stop offset="0" stop-color="${palette.outer[0]}"/><stop offset=".55" stop-color="${palette.outer[1]}"/><stop offset="1" stop-color="${palette.outer[2]}"/></linearGradient></defs><path d="${variant.qPath}" fill="none" stroke="url(#q)" stroke-width="${strokeWidth}" stroke-linecap="round"/>${plane}${grid}<path d="${variant.trajectoryPath}" fill="none" stroke="${palette.trajectory}" stroke-width="${detail === 'micro' ? 4 : 3.5}" stroke-linecap="round"/>${nodes}</svg>\n`;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const name = Buffer.from(type, 'ascii');
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  name.copy(out, 4);
  data.copy(out, 8);
  out.writeUInt32BE(crc32(Buffer.concat([name, data])), 8 + data.length);
  return out;
}

function microPng(size = 32) {
  const rgba = Buffer.alloc(size * size * 4);
  const set = (x, y, color) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    const offset = (y * size + x) * 4;
    rgba.set(color, offset);
  };
  const blue = [37, 99, 235, 255];
  const cyan = [14, 165, 233, 255];
  const teal = [45, 212, 191, 255];
  const line = (x1, y1, x2, y2, width, color) => {
    const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1) * 2);
    for (let step = 0; step <= steps; step += 1) {
      const x = x1 + ((x2 - x1) * step) / steps;
      const y = y1 + ((y2 - y1) * step) / steps;
      for (let dy = -width; dy <= width; dy += 1) for (let dx = -width; dx <= width; dx += 1) if (dx * dx + dy * dy <= width * width) set(Math.round(x + dx), Math.round(y + dy), color);
    }
  };
  const dot = (cx, cy, radius, color) => {
    for (let y = -radius; y <= radius; y += 1) for (let x = -radius; x <= radius; x += 1) if (x * x + y * y <= radius * radius) set(cx + x, cy + y, color);
  };
  const cx = 15.5;
  const cy = 15.5;
  for (let y = 0; y < size; y += 1) for (let x = 0; x < size; x += 1) {
    const dx = x - cx;
    const dy = y - cy;
    const radius = Math.hypot(dx, dy);
    const angle = Math.atan2(dy, dx);
    if (radius >= 10.5 && radius <= 14.5 && !(angle > 0.42 && angle < 1.08)) set(x, y, angle < -0.4 ? cyan : blue);
  }
  line(21, 21, 28, 28, 2, cyan);
  line(9, 18, 21, 15, 1, teal);
  dot(9, 18, 2, blue);
  dot(21, 15, 2, teal);
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y += 1) {
    const row = y * (size * 4 + 1);
    raw[row] = 0;
    rgba.copy(raw, row + 1, y * size * 4, (y + 1) * size * 4);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), pngChunk('IHDR', ihdr), pngChunk('IDAT', deflateSync(raw)), pngChunk('IEND', Buffer.alloc(0))]);
}

function ico() {
  const image = microPng(32);
  const header = Buffer.alloc(22);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(1, 4);
  header[6] = 32;
  header[7] = 32;
  header.writeUInt16LE(1, 10);
  header.writeUInt16LE(32, 12);
  header.writeUInt32LE(image.length, 14);
  header.writeUInt32LE(22, 18);
  return Buffer.concat([header, image]);
}

const outputs = new Map();
for (const detail of ['master', 'compact', 'micro']) for (const tone of ['dark', 'light', 'mono-dark', 'mono-light']) outputs.set(`public/brand/qualibug-behavior-field-${detail}-${tone}.svg`, Buffer.from(svg(detail, tone)));
outputs.set('public/favicon.svg', Buffer.from(svg('micro', 'light')));
outputs.set('public/favicon.ico', ico());

const drift = [];
for (const [relative, expected] of outputs) {
  const path = resolve(root, relative);
  if (checkOnly) {
    let actual;
    try { actual = readFileSync(path); } catch { drift.push(`${relative}: missing`); continue; }
    if (!actual.equals(expected)) drift.push(`${relative}: stale or malformed`);
  } else {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, expected);
  }
}
if (drift.length) throw new Error(`Brand asset check failed:\n${drift.join('\n')}`);
console.log(checkOnly ? 'PASS brand:check' : `WROTE ${outputs.size} governed brand assets`);
```

- [ ] **Step 3: Verify the check fails before export**

Run from `frontend`:

```powershell
npm run brand:check
```

Expected: FAIL and list missing `public/brand/*`, `public/favicon.svg`, and `public/favicon.ico` outputs.

- [ ] **Step 4: Export assets and verify exact drift checks**

Run:

```powershell
npm run brand:export
npm run brand:check
npm run build
```

Expected: exporter writes `14` assets; check and build exit `0`. `prebuild` runs `brand:check` before Vite.

- [ ] **Step 5: Verify served favicon content, not SPA fallback**

With the Vite development server on `5174`, run from the repository root:

```powershell
$response = Invoke-WebRequest -Uri 'http://127.0.0.1:5174/favicon.svg' -UseBasicParsing
if ($response.Headers['Content-Type'] -notlike 'image/svg+xml*') { throw "wrong favicon content type: $($response.Headers['Content-Type'])" }
if (-not $response.Content.TrimStart().StartsWith('<svg')) { throw 'favicon returned non-SVG content' }
```

Expected: no exception.

- [ ] **Step 6: Commit Task 2**

```powershell
git add frontend/scripts/brand/export-assets.mjs frontend/public/brand frontend/public/favicon.svg frontend/public/favicon.ico frontend/package.json
git commit -m "feat: generate governed brand assets"
```

---

### Task 3: Deterministic Behavior-Space Login Visual

**Files:**
- Create: `frontend/scripts/brand/behavior-space-canvas-contract.mjs`
- Create: `frontend/src/brand/behaviorFieldCanvas.ts`
- Modify: `frontend/src/components/LoginStageCanvas.tsx`
- Modify: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/index.css`
- Modify: `frontend/scripts/login-page-contract.mjs`
- Modify: `frontend/package.json`

**Interfaces:**
- Produces: `BehaviorFieldFrame` and `drawBehaviorFieldFrame(context, frame): void`.
- Produces: `data-brand-visual-state="initializing|ready|reduced-motion|failed"`.
- Preserves: all existing visible login copy and health behavior.

- [ ] **Step 1: Write the failing source contract**

Create `frontend/scripts/brand/behavior-space-canvas-contract.mjs`:

```js
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const canvas = readFileSync(resolve(root, 'src/components/LoginStageCanvas.tsx'), 'utf8');
const renderer = readFileSync(resolve(root, 'src/brand/behaviorFieldCanvas.ts'), 'utf8');
const login = readFileSync(resolve(root, 'src/pages/Login.tsx'), 'utf8');
const css = readFileSync(resolve(root, 'src/index.css'), 'utf8');

for (const token of ['data-brand-visual-state', '[login.brand-visual]', 'drawBehaviorFieldFrame']) {
  if (!canvas.includes(token)) throw new Error(`Observable canvas contract missing: ${token}`);
}
for (const token of ['BEHAVIOR_NODES', 'BEHAVIOR_TRAJECTORY', 'drawPerspectivePlane']) {
  if (!renderer.includes(token)) throw new Error(`Behavior-space renderer missing: ${token}`);
}
for (const forbidden of ['Math.random', 'drawRadar', 'PulseRing', 'type Particle', 'type Beam', 'linkDist']) {
  if (canvas.includes(forbidden) || renderer.includes(forbidden)) throw new Error(`Crawler/radar visual remains: ${forbidden}`);
}
if (login.includes('login-stage-scan')) throw new Error('Scan sweep DOM remains');
if (css.includes('@keyframes scan-sweep') || css.includes('.login-stage-scan')) throw new Error('Scan sweep CSS remains');
console.log('PASS behavior-space-canvas-contract');
```

Add:

```json
"test:brand-canvas": "node scripts/brand/behavior-space-canvas-contract.mjs"
```

- [ ] **Step 2: Freeze existing copy and add observable browser assertions before implementation**

In `frontend/scripts/login-page-contract.mjs`, add a login-only copy assertion:

```js
async function assertCurrentCopy(page, context) {
  for (const text of [
    'EVIDENCE-DRIVEN QUALITY',
    '上线前，先看清',
    '业务会不会出事',
    '把软件风险变成可复现、可验收、可决策的业务结论。',
    '发现真问题',
    '验证后再交付',
    '结论有证据',
    '影响与复现可追溯',
    '发布有依据',
    '风险门禁清晰可见'
  ]) await expectCount(page.getByText(text, { exact: true }), 1, { ...context, text });
  await expectCount(page.locator('[data-brand-detail="compact"][data-brand-tone="dark"]'), 1, context);
  const visual = page.locator('[data-brand-visual-state]');
  await expectCount(visual, 1, context);
  const state = await visual.getAttribute('data-brand-visual-state');
  if (!['ready', 'reduced-motion'].includes(state)) fail('Brand visual did not become ready', { ...context, state });
}
```

Call it inside the existing `if (mode === 'login')` block.

Add separate reduced-motion and failed-context checks after the normal viewport loop:

```js
const reducedContext = await browser.newContext({ reducedMotion: 'reduce' });
const reducedPage = await reducedContext.newPage();
await installHealthyApi(reducedPage);
await reducedPage.goto(baseUrl, { waitUntil: 'domcontentloaded' });
await expectCount(reducedPage.locator('[data-brand-visual-state="reduced-motion"]'), 1, { mode: 'reduced-motion' });
await reducedContext.close();

const failedContext = await browser.newContext();
const failedPage = await failedContext.newPage();
const brandErrors = [];
failedPage.on('console', (message) => { if (message.type() === 'error' && message.text().includes('[login.brand-visual]')) brandErrors.push(message.text()); });
await failedPage.addInitScript(() => { HTMLCanvasElement.prototype.getContext = () => null; });
await installHealthyApi(failedPage);
await failedPage.goto(baseUrl, { waitUntil: 'domcontentloaded' });
await expectCount(failedPage.locator('[data-brand-visual-state="failed"]'), 1, { mode: 'canvas-failure' });
if (brandErrors.length === 0) fail('Canvas failure was not logged', { mode: 'canvas-failure' });
await failedContext.close();
```

- [ ] **Step 3: Run the new contracts and verify failure**

Run:

```powershell
npm run test:brand-canvas
npm run test:login-contract
```

Expected: source contract fails because the renderer does not exist; browser contract fails because the visual-state attribute does not exist.

- [ ] **Step 4: Implement the pure deterministic renderer**

Create `frontend/src/brand/behaviorFieldCanvas.ts` with normalized, industry-neutral points and no random source:

```ts
export type BehaviorFieldFrame = {
  width: number;
  height: number;
  time: number;
  pointerX: number;
  pointerY: number;
  focusBoost: number;
  reducedMotion: boolean;
};

export const BEHAVIOR_NODES = [
  { u: 0.08, v: 0.74 },
  { u: 0.34, v: 0.58 },
  { u: 0.62, v: 0.35 },
  { u: 0.9, v: 0.48 },
] as const;

export const BEHAVIOR_TRAJECTORY = BEHAVIOR_NODES;

type Point = { x: number; y: number };

function project(u: number, v: number, origin: Point, scaleX: number, scaleY: number): Point {
  return { x: origin.x + (u - v) * scaleX, y: origin.y + (u + v) * scaleY };
}

export function drawPerspectivePlane(context: CanvasRenderingContext2D, frame: BehaviorFieldFrame): Point[] {
  const driftX = (frame.pointerX - 0.5) * 18;
  const driftY = (frame.pointerY - 0.5) * 10;
  const origin = { x: frame.width * 0.2 + driftX, y: frame.height * 0.48 + driftY };
  const scaleX = Math.min(frame.width * 0.38, 340);
  const scaleY = Math.min(frame.height * 0.18, 115);
  context.save();
  context.strokeStyle = 'rgba(56,189,248,.18)';
  context.lineWidth = 1;
  for (let index = 0; index <= 6; index += 1) {
    const ratio = index / 6;
    const a = project(ratio, 0, origin, scaleX, scaleY);
    const b = project(ratio, 1, origin, scaleX, scaleY);
    const c = project(0, ratio, origin, scaleX, scaleY);
    const d = project(1, ratio, origin, scaleX, scaleY);
    context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.stroke();
    context.beginPath(); context.moveTo(c.x, c.y); context.lineTo(d.x, d.y); context.stroke();
  }
  context.restore();
  return BEHAVIOR_NODES.map((node) => project(node.u, node.v, origin, scaleX, scaleY));
}

export function drawBehaviorFieldFrame(context: CanvasRenderingContext2D, frame: BehaviorFieldFrame): void {
  context.clearRect(0, 0, frame.width, frame.height);
  const nodes = drawPerspectivePlane(context, frame);
  context.save();
  context.strokeStyle = '#7DD3FC';
  context.lineWidth = 2.5 + frame.focusBoost;
  context.lineCap = 'round';
  context.beginPath();
  context.moveTo(nodes[0].x, nodes[0].y);
  context.bezierCurveTo(nodes[1].x - 18, nodes[1].y + 8, nodes[1].x - 8, nodes[1].y + 2, nodes[1].x, nodes[1].y);
  context.bezierCurveTo(nodes[2].x - 12, nodes[2].y + 14, nodes[2].x - 8, nodes[2].y + 2, nodes[2].x, nodes[2].y);
  context.bezierCurveTo(nodes[3].x - 12, nodes[3].y - 8, nodes[3].x - 5, nodes[3].y, nodes[3].x, nodes[3].y);
  context.stroke();
  nodes.forEach((node, index) => {
    const pulse = frame.reducedMotion ? 0 : Math.sin(frame.time * 0.002 + index * 0.8) * 0.8;
    context.beginPath();
    context.arc(node.x, node.y, 4.5 + pulse + frame.focusBoost * 0.5, 0, Math.PI * 2);
    context.fillStyle = index === 2 ? '#2DD4BF' : '#0EA5E9';
    context.fill();
  });
  context.restore();
}
```

- [ ] **Step 5: Replace the canvas lifecycle with observable fail-fast behavior**

Replace `frontend/src/components/LoginStageCanvas.tsx` with:

```tsx
import { useEffect, useRef, useState } from 'react';
import { drawBehaviorFieldFrame } from '../brand/behaviorFieldCanvas';

type LoginStageCanvasProps = {
  pointerX: number;
  pointerY: number;
  focusBoost?: boolean;
};

type BrandVisualState = 'initializing' | 'ready' | 'reduced-motion' | 'failed';

export function LoginStageCanvas({ pointerX, pointerY, focusBoost = false }: LoginStageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pointerRef = useRef({ x: 0.28, y: 0.48 });
  const focusRef = useRef(false);
  const [visualState, setVisualState] = useState<BrandVisualState>('initializing');

  useEffect(() => {
    pointerRef.current = { x: pointerX, y: pointerY };
  }, [pointerX, pointerY]);

  useEffect(() => {
    focusRef.current = focusBoost;
  }, [focusBoost]);

  useEffect(() => {
    const canvas = canvasRef.current;
    let stopped = false;
    let raf = 0;
    let width = 0;
    let height = 0;
    let boost = 0;
    let publishedState: BrandVisualState = 'initializing';

    const publish = (state: BrandVisualState) => {
      if (publishedState === state || stopped) return;
      publishedState = state;
      setVisualState(state);
    };

    const fail = (operation: 'initialize' | 'resize' | 'render', reason: string, error?: unknown) => {
      console.error('[login.brand-visual] Behavior Field canvas failed', { operation, reason, error });
      stopped = true;
      window.cancelAnimationFrame(raf);
      publishedState = 'failed';
      setVisualState('failed');
    };

    if (!canvas) {
      fail('initialize', 'canvas_element_unavailable');
      return;
    }
    const context = canvas.getContext('2d');
    if (!context) {
      fail('initialize', '2d_context_unavailable');
      return;
    }

    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let reducedMotion = media.matches;

    const resize = (): boolean => {
      const parent = canvas.parentElement;
      if (!parent) {
        fail('resize', 'parent_element_unavailable');
        return false;
      }
      const rect = parent.getBoundingClientRect();
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      return true;
    };

    const render = (time: number) => {
      if (stopped) return;
      boost += ((focusRef.current ? 1 : 0) - boost) * 0.06;
      try {
        drawBehaviorFieldFrame(context, {
          width,
          height,
          time,
          pointerX: pointerRef.current.x,
          pointerY: pointerRef.current.y,
          focusBoost: boost,
          reducedMotion,
        });
        publish(reducedMotion ? 'reduced-motion' : 'ready');
      } catch (error) {
        fail('render', 'draw_failed', error);
        return;
      }
      if (!reducedMotion) raf = window.requestAnimationFrame(render);
    };

    const restart = () => {
      window.cancelAnimationFrame(raf);
      if (!resize() || stopped) return;
      if (reducedMotion) render(0);
      else raf = window.requestAnimationFrame(render);
    };

    const onMotionChange = () => {
      reducedMotion = media.matches;
      restart();
    };

    setVisualState('initializing');
    restart();
    window.addEventListener('resize', restart);
    media.addEventListener?.('change', onMotionChange);

    return () => {
      stopped = true;
      window.cancelAnimationFrame(raf);
      window.removeEventListener('resize', restart);
      media.removeEventListener?.('change', onMotionChange);
    };
  }, []);

  return <canvas ref={canvasRef} className="login-stage-canvas" aria-hidden="true" data-brand-visual-state={visualState} />;
}
```

- [ ] **Step 6: Remove only obsolete scan visual DOM/CSS**

Delete this visual-only line from `Login.tsx` without changing adjacent text:

```tsx
<div className="login-stage-scan" aria-hidden="true" />
```

Delete `@keyframes scan-sweep`, `.login-stage-scan`, and its reduced-motion/mobile references from `index.css`. Keep the current copy, layout, glows, health badge, form, and responsive rules.

- [ ] **Step 7: Run focused verification**

Run from `frontend`:

```powershell
npm run test:brand-canvas
npm run test:login-contract
npm run typecheck
npm run lint
```

Expected: all exit `0`; login contract reports `3 modes × 2 viewports` and the additional reduced-motion/failure checks pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add frontend/src/brand/behaviorFieldCanvas.ts frontend/src/components/LoginStageCanvas.tsx frontend/src/pages/Login.tsx frontend/src/index.css frontend/scripts/brand/behavior-space-canvas-contract.mjs frontend/scripts/login-page-contract.mjs frontend/package.json
git commit -m "feat: render deterministic behavior space"
```

---

### Task 4: Living Brand Documentation

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: a root Brand Direction Contract for future agents and engineers.

- [ ] **Step 1: Add a failing documentation assertion**

Before editing `AGENTS.md`, run:

```powershell
$agents = Get-Content -Raw -Encoding utf8 'AGENTS.md'
foreach ($required in @('## Brand Direction Contract','enterprise software behavior-space infrastructure','no insect, crawler, spider-web, radar, or scraping semantics','frontend 5174 and backend 8088')) {
  if (-not $agents.Contains($required)) { throw "missing brand contract: $required" }
}
```

Expected: FAIL on the first missing phrase.

- [ ] **Step 2: Add the Brand Direction Contract**

Append this section to root `AGENTS.md`:

```markdown
## Brand Direction Contract

- QualiBug AI is enterprise software behavior-space infrastructure. It maps actors, states, data, rules, and real execution trajectories into a computable, verifiable, evolvable behavior-space model.
- The governed Behavior Field mark is the brand source of truth: Q is the enterprise-system boundary, the plane is behavior space, nodes are states, and the curve is an observed behavior trajectory.
- Brand and decorative product visuals use no insect, crawler, spider-web, radar, or scraping semantics. `Bug` means a verified divergence between observed and expected behavior.
- Brand animation is decorative only and must never imply service, provider, campaign, scan, model, evaluator, or commercial health.
- Brand work remains industry-neutral, preserves existing product copy unless separately approved, and keeps frontend 5174 and backend 8088.
```

- [ ] **Step 3: Re-run the documentation assertion**

Run the Step 1 PowerShell assertion again.

Expected: no exception.

- [ ] **Step 4: Commit Task 4**

```powershell
git add AGENTS.md
git commit -m "docs: record behavior-space brand contract"
```

---

### Task 5: Full Verification and Live Product Inspection

**Files:**
- Verify only; do not create new files unless a failing check identifies a root-cause defect in the files already listed.

**Interfaces:**
- Consumes all prior task outputs.
- Produces evidence that the approved brand works in the running product.

- [ ] **Step 1: Run all focused and production checks**

From `frontend`:

```powershell
npm run test:brand-mark
npm run brand:check
npm run test:brand-canvas
npm run test:login-contract
npm run typecheck
npm run lint
npm run build
```

Expected: every command exits `0` with no warning promoted to an error.

- [ ] **Step 2: Verify ports and favicon response**

From repository root:

```powershell
$listeners = Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -in 5174,8088 }
$listeners | Select-Object LocalAddress,LocalPort,OwningProcess
$favicon = Invoke-WebRequest -Uri 'http://127.0.0.1:5174/favicon.svg' -UseBasicParsing
if ($favicon.Headers['Content-Type'] -notlike 'image/svg+xml*') { throw "favicon content type is $($favicon.Headers['Content-Type'])" }
if (-not $favicon.Content.TrimStart().StartsWith('<svg')) { throw 'favicon is not SVG' }
```

Expected: frontend listener is `5174`; backend, when running, is `8088`; favicon is an SVG image rather than HTML.

- [ ] **Step 3: Inspect the live login page at desktop width**

Open `http://127.0.0.1:5174/login` and verify:

- the existing hero, proof-card, and form copy is unchanged;
- the Q Behavior Field mark replaces the literal insect;
- the background shows a perspective plane and deterministic trajectory;
- no radar ring, scan wedge, beam, random mesh, or crawling cue remains;
- health visibly reports the real current result;
- successful-path console has no brand errors.

- [ ] **Step 4: Inspect mobile and reduced-motion modes**

At `390×844` and with reduced motion enabled, verify:

- no horizontal overflow;
- compact mark remains legible;
- form remains complete;
- canvas state is `reduced-motion` and static;
- current copy remains unchanged.

- [ ] **Step 5: Verify repository scope**

Run:

```powershell
git status --short
git diff --check HEAD~4..HEAD
git log -4 --oneline
```

Expected: only plan-listed files are committed by this work; unrelated pre-existing changes remain untouched.

---

## Plan Self-Review Checklist

- Spec coverage: mark semantics, adaptive variants, palette, asset SSOT, favicon, no-crawler boundary, deterministic canvas, copy freeze, observability, accessibility, responsive checks, documentation, and ports each map to a task.
- Placeholder scan: the plan contains no deferred implementation markers.
- Type consistency: `BrandDetail`, `BrandTone`, `BehaviorFieldFrame`, `drawBehaviorFieldFrame`, and `data-brand-visual-state` use the same names in producers, consumers, and tests.
- Scope: no backend contract, authentication behavior, visible product copy, dependency, benchmark data, or industry rule changes.
