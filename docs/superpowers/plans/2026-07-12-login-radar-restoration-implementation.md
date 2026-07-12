# Login Radar Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the complete earlier radar composition on the login stage while retaining the Behavior Field logo, current copy, deterministic rendering, reduced-motion behavior, and observable canvas failures.

**Architecture:** A focused `loginRadarCanvas.ts` module owns deterministic scene creation and canvas drawing; `LoginStageCanvas.tsx` owns only React lifecycle, sizing, motion preference, and observable state. The existing login page restores the scan-light layer, while static and browser contracts freeze visual semantics, copy, responsive behavior, and failure visibility.

**Tech Stack:** React 19, TypeScript 5, Canvas 2D, CSS, Node.js contract scripts, Playwright, Vite 8.

## Global Constraints

- Do not use subagents; repository instructions require inline execution in the current session.
- Create an isolated worktree with `superpowers:using-git-worktrees` before implementation because the main checkout contains unrelated work.
- Keep the governed Behavior Field mark unchanged in login, sidebar, reports, favicons, and exported assets.
- Restore the complete radar composition: circular rings, rotating sweep, pulse rings, connected state particles, moving light beams, grid, and vertical scan-light.
- Preserve every visible login, registration, reset, health, navigation, and product string exactly.
- Preserve authentication behavior and API contracts exactly.
- The radar represents enterprise-system behavior observation only; no insect, crawler, spider-web, scraping, customer, benchmark, industry, or hidden-evaluator semantics.
- Decorative animation must never imply service, provider, model, campaign, evaluator, or commercial health.
- Keep frontend port `5174` and backend port `8088`; use `4173` only for isolated preview verification.
- Do not add dependencies or use `Math.random` in the radar scene.
- Keep `data-brand-visual-state="initializing|ready|reduced-motion|failed"` and `[login.brand-visual]` structured failure logging.
- Missing canvas, missing 2D context, resize failure, scene creation failure, and draw failure must publish `failed`; none may silently return.
- Cap device-pixel ratio at `2`, particle count at `68`, and beam count at `8`.
- After every TypeScript or TSX edit, run `npm run typecheck` before proceeding.
- Stage only files named by the active task; preserve all unrelated dirty files.

## File Structure

- Create `frontend/src/visuals/loginRadarCanvas.ts` — deterministic radar scene model and Canvas 2D renderer.
- Modify `frontend/src/components/LoginStageCanvas.tsx` — React lifecycle, resize, reduced motion, observable failures, and renderer orchestration.
- Modify `frontend/src/pages/Login.tsx` — restore the decorative scan-light DOM layer only.
- Modify `frontend/src/index.css` — restore scan-light animation and responsive/reduced-motion rules.
- Create `frontend/scripts/brand/login-radar-canvas-contract.mjs` — focused source contract for renderer, integration, semantics, and living documentation.
- Delete `frontend/scripts/brand/behavior-space-canvas-contract.mjs` — obsolete perspective-plane contract.
- Delete `frontend/src/brand/behaviorFieldCanvas.ts` — obsolete login perspective-plane renderer.
- Modify `frontend/scripts/login-page-contract.mjs` — verify radar and scan-light behavior in desktop/mobile browsers.
- Modify `frontend/package.json` — add the focused radar command while keeping the existing brand-canvas alias compatible.
- Modify `AGENTS.md` — record the approved radar exception without weakening the no-crawler or honest-health rules.

---

### Task 1: Deterministic Login Radar Renderer

**Files:**
- Create: `frontend/scripts/brand/login-radar-canvas-contract.mjs`
- Create: `frontend/src/visuals/loginRadarCanvas.ts`
- Modify: `frontend/package.json:19`

**Interfaces:**
- Produces: `createSeededRandom(seed: number): () => number`
- Produces: `createLoginRadarScene(width: number, height: number): LoginRadarScene`
- Produces: `drawLoginRadarFrame(context: CanvasRenderingContext2D, scene: LoginRadarScene, frame: LoginRadarFrame): void`
- Produces: `LoginRadarScene` and `LoginRadarFrame` types for Task 2.

- [ ] **Step 1: Add the focused contract and package command before the renderer exists**

Create `frontend/scripts/brand/login-radar-canvas-contract.mjs`:

```js
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const rendererPath = resolve(root, 'src/visuals/loginRadarCanvas.ts');

if (!existsSync(rendererPath)) {
  throw new Error('Login radar renderer missing: src/visuals/loginRadarCanvas.ts');
}

const renderer = readFileSync(rendererPath, 'utf8');
for (const token of [
  'createSeededRandom',
  'createLoginRadarScene',
  'drawLoginRadarFrame',
  'drawGrid',
  'drawRadar',
  'drawParticles',
  'drawBeams',
  'PulseRing',
]) {
  if (!renderer.includes(token)) {
    throw new Error(`Login radar renderer contract missing: ${token}`);
  }
}

if (renderer.includes('Math.random')) {
  throw new Error('Login radar renderer must be deterministic; Math.random is forbidden');
}

console.log('PASS login-radar-canvas-contract');
```

In `frontend/package.json`, replace the existing canvas command and retain a compatibility alias:

```json
"test:login-radar": "node scripts/brand/login-radar-canvas-contract.mjs",
"test:brand-canvas": "npm run test:login-radar",
```

- [ ] **Step 2: Run the focused contract and verify the intended red state**

Run:

```powershell
cd frontend
npm run test:login-radar
```

Expected: non-zero exit with `Login radar renderer missing: src/visuals/loginRadarCanvas.ts`.

- [ ] **Step 3: Implement the deterministic radar renderer**

Create `frontend/src/visuals/loginRadarCanvas.ts`:

```ts
export type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  pulse: number;
};

export type Beam = {
  x: number;
  y: number;
  len: number;
  speed: number;
  angle: number;
  alpha: number;
};

export type PulseRing = {
  radius: number;
  max: number;
  alpha: number;
};

export type LoginRadarScene = {
  width: number;
  height: number;
  particles: Particle[];
  beams: Beam[];
  rings: PulseRing[];
  lastRingAt: number;
  random: () => number;
};

export type LoginRadarFrame = {
  time: number;
  pointerX: number;
  pointerY: number;
  focusBoost: number;
  reducedMotion: boolean;
};

export function createSeededRandom(seed: number): () => number {
  let state = seed >>> 0;
  if (state === 0) state = 0x6d2b79f5;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function assertDimensions(width: number, height: number): void {
  if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
    throw new Error(`Login radar dimensions must be positive finite numbers: ${width}x${height}`);
  }
}

function sceneSeed(width: number, height: number): number {
  return (Math.imul(Math.floor(width), 73856093) ^ Math.imul(Math.floor(height), 19349663)) >>> 0;
}

function createBeam(random: () => number, width: number, height: number): Beam {
  return {
    x: random() * width * 0.7,
    y: random() * height,
    len: 40 + random() * 90,
    speed: 1.4 + random() * 2,
    angle: -0.4 - random() * 0.4,
    alpha: 0.18 + random() * 0.28,
  };
}

export function createLoginRadarScene(width: number, height: number): LoginRadarScene {
  assertDimensions(width, height);
  const random = createSeededRandom(sceneSeed(width, height));
  const count = Math.max(36, Math.min(68, Math.floor((width * height) / 14000)));
  const particles = Array.from({ length: count }, (): Particle => ({
    x: random() * width,
    y: random() * height,
    vx: (random() - 0.5) * 0.55,
    vy: (random() - 0.5) * 0.55,
    r: 1.2 + random() * 2,
    pulse: random() * Math.PI * 2,
  }));
  const beams = Array.from({ length: 8 }, () => createBeam(random, width, height));
  return { width, height, particles, beams, rings: [], lastRingAt: 0, random };
}

function drawGrid(
  context: CanvasRenderingContext2D,
  scene: LoginRadarScene,
  frame: LoginRadarFrame,
): void {
  const drift = frame.reducedMotion ? 0 : (frame.time * 0.018) % 48;
  context.save();
  context.strokeStyle = `rgba(45, 212, 191, ${0.06 + frame.focusBoost * 0.04})`;
  context.lineWidth = 1;
  for (let x = -48 + drift; x <= scene.width + 48; x += 48) {
    context.beginPath();
    context.moveTo(x, 0);
    context.lineTo(x, scene.height);
    context.stroke();
  }
  for (let y = -48 + drift * 0.65; y <= scene.height + 48; y += 48) {
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(scene.width, y);
    context.stroke();
  }
  context.restore();
}

function resetBeam(scene: LoginRadarScene, beam: Beam): void {
  beam.x = scene.random() * scene.width * 0.7;
  beam.y = scene.height + 40;
  beam.len = 40 + scene.random() * 90;
  beam.speed = 1.4 + scene.random() * 2;
  beam.alpha = 0.18 + scene.random() * 0.28;
}

function drawBeams(
  context: CanvasRenderingContext2D,
  scene: LoginRadarScene,
  frame: LoginRadarFrame,
): void {
  for (const beam of scene.beams) {
    if (!frame.reducedMotion) {
      beam.x += Math.cos(beam.angle) * beam.speed * (1 + frame.focusBoost * 0.25);
      beam.y += Math.sin(beam.angle) * beam.speed * (1 + frame.focusBoost * 0.25);
      if (
        beam.x < -120
        || beam.y < -120
        || beam.x > scene.width + 120
        || beam.y > scene.height + 120
      ) {
        resetBeam(scene, beam);
      }
    }
    const endX = beam.x + Math.cos(beam.angle) * beam.len;
    const endY = beam.y + Math.sin(beam.angle) * beam.len;
    const gradient = context.createLinearGradient(beam.x, beam.y, endX, endY);
    gradient.addColorStop(0, 'rgba(45, 212, 191, 0)');
    gradient.addColorStop(0.5, `rgba(94, 234, 212, ${beam.alpha * (1 + frame.focusBoost * 0.35)})`);
    gradient.addColorStop(1, 'rgba(186, 230, 253, 0)');
    context.strokeStyle = gradient;
    context.lineWidth = 1.6;
    context.beginPath();
    context.moveTo(beam.x, beam.y);
    context.lineTo(endX, endY);
    context.stroke();
  }
}

function drawParticles(
  context: CanvasRenderingContext2D,
  scene: LoginRadarScene,
  frame: LoginRadarFrame,
  centerX: number,
  centerY: number,
): void {
  const linkDistance = Math.min(150, Math.max(100, scene.width * 0.11));
  for (let index = 0; index < scene.particles.length; index += 1) {
    const particle = scene.particles[index];
    if (!frame.reducedMotion) {
      particle.vx += (centerX - particle.x) * (0.000012 + frame.focusBoost * 0.00001);
      particle.vy += (centerY - particle.y) * (0.000012 + frame.focusBoost * 0.00001);
      particle.x += particle.vx;
      particle.y += particle.vy;
      if (particle.x < -20) particle.x = scene.width + 20;
      if (particle.x > scene.width + 20) particle.x = -20;
      if (particle.y < -20) particle.y = scene.height + 20;
      if (particle.y > scene.height + 20) particle.y = -20;
    }

    for (let targetIndex = index + 1; targetIndex < scene.particles.length; targetIndex += 1) {
      const target = scene.particles[targetIndex];
      const deltaX = particle.x - target.x;
      const deltaY = particle.y - target.y;
      const distance = Math.hypot(deltaX, deltaY);
      if (distance < linkDistance) {
        const alpha = (1 - distance / linkDistance) * (0.28 + frame.focusBoost * 0.12);
        context.beginPath();
        context.moveTo(particle.x, particle.y);
        context.lineTo(target.x, target.y);
        context.strokeStyle = `rgba(94, 234, 212, ${alpha})`;
        context.lineWidth = 1;
        context.stroke();
      }
    }

    const glow = 0.45 + Math.sin(frame.time * 0.004 + particle.pulse) * 0.3;
    context.beginPath();
    context.arc(particle.x, particle.y, particle.r, 0, Math.PI * 2);
    context.fillStyle = `rgba(167, 243, 208, ${0.35 + glow * 0.4})`;
    context.shadowColor = 'rgba(45, 212, 191, 0.55)';
    context.shadowBlur = 8;
    context.fill();
    context.shadowBlur = 0;
  }
}

function drawRadar(
  context: CanvasRenderingContext2D,
  scene: LoginRadarScene,
  frame: LoginRadarFrame,
  centerX: number,
  centerY: number,
): void {
  const radius = Math.min(scene.width, scene.height) * (0.34 + frame.focusBoost * 0.02);
  const glowMultiplier = 1 + frame.focusBoost * 0.45;
  context.save();

  for (let index = 1; index <= 4; index += 1) {
    context.beginPath();
    context.arc(centerX, centerY, radius * (0.26 + index * 0.18), 0, Math.PI * 2);
    context.strokeStyle = `rgba(45, 212, 191, ${(0.12 + index * 0.04) * glowMultiplier})`;
    context.lineWidth = index === 4 ? 1.6 : 1;
    context.stroke();
  }

  context.strokeStyle = `rgba(94, 234, 212, ${0.16 * glowMultiplier})`;
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(centerX - radius, centerY);
  context.lineTo(centerX + radius, centerY);
  context.moveTo(centerX, centerY - radius);
  context.lineTo(centerX, centerY + radius);
  context.stroke();

  const core = context.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
  core.addColorStop(0, `rgba(45, 212, 191, ${0.22 * glowMultiplier})`);
  core.addColorStop(0.4, `rgba(14, 165, 233, ${0.1 * glowMultiplier})`);
  core.addColorStop(1, 'rgba(45, 212, 191, 0)');
  context.fillStyle = core;
  context.beginPath();
  context.arc(centerX, centerY, radius, 0, Math.PI * 2);
  context.fill();

  const angle = frame.reducedMotion ? -0.35 : frame.time * (0.0015 + frame.focusBoost * 0.0006);
  context.save();
  context.translate(centerX, centerY);
  context.rotate(angle);
  const wedge = context.createLinearGradient(0, 0, radius, 0);
  wedge.addColorStop(0, `rgba(94, 234, 212, ${0.5 + frame.focusBoost * 0.2})`);
  wedge.addColorStop(0.35, `rgba(56, 189, 248, ${0.18 + frame.focusBoost * 0.1})`);
  wedge.addColorStop(1, 'rgba(45, 212, 191, 0)');
  context.fillStyle = wedge;
  context.beginPath();
  context.moveTo(0, 0);
  context.arc(0, 0, radius, -0.55, 0.12);
  context.closePath();
  context.fill();
  context.strokeStyle = 'rgba(204, 251, 241, 0.85)';
  context.lineWidth = 1.8;
  context.shadowColor = 'rgba(45, 212, 191, 0.7)';
  context.shadowBlur = 10 + frame.focusBoost * 8;
  context.beginPath();
  context.moveTo(0, 0);
  context.lineTo(radius, 0);
  context.stroke();
  context.shadowBlur = 0;
  context.restore();

  for (let index = 0; index < 5; index += 1) {
    const dotAngle = angle * 0.7 + index * 1.15;
    const distance = radius * (0.3 + ((index * 41) % 55) / 100);
    const x = centerX + Math.cos(dotAngle) * distance;
    const y = centerY + Math.sin(dotAngle) * distance;
    const blink = frame.reducedMotion ? 0.7 : 0.35 + Math.abs(Math.sin(frame.time * 0.005 + index)) * 0.5;
    context.beginPath();
    context.arc(x, y, 2.4, 0, Math.PI * 2);
    context.fillStyle = `rgba(94, 234, 212, ${blink})`;
    context.shadowColor = 'rgba(45, 212, 191, 0.8)';
    context.shadowBlur = 8;
    context.fill();
    context.shadowBlur = 0;
  }

  const ringGap = frame.focusBoost > 0.5 ? 1100 : 1600;
  if (!frame.reducedMotion && frame.time - scene.lastRingAt > ringGap) {
    scene.rings.push({ radius: 8, max: radius * 1.15, alpha: 0.45 + frame.focusBoost * 0.2 });
    scene.lastRingAt = frame.time;
  }
  scene.rings = scene.rings.filter((ring) => ring.alpha > 0.02 && ring.radius <= ring.max);
  for (const ring of scene.rings) {
    if (!frame.reducedMotion) {
      ring.radius += 1.6 + frame.focusBoost * 0.6;
      ring.alpha *= 0.985;
    }
    context.beginPath();
    context.arc(centerX, centerY, ring.radius, 0, Math.PI * 2);
    context.strokeStyle = `rgba(45, 212, 191, ${ring.alpha})`;
    context.lineWidth = 1.6;
    context.stroke();
  }

  context.beginPath();
  context.arc(centerX, centerY, 4.5, 0, Math.PI * 2);
  context.fillStyle = '#99f6e4';
  context.shadowColor = 'rgba(94, 234, 212, 0.9)';
  context.shadowBlur = 16 + frame.focusBoost * 10;
  context.fill();
  context.restore();
}

export function drawLoginRadarFrame(
  context: CanvasRenderingContext2D,
  scene: LoginRadarScene,
  frame: LoginRadarFrame,
): void {
  context.clearRect(0, 0, scene.width, scene.height);
  drawGrid(context, scene, frame);
  const centerX = scene.width * (0.22 + (frame.pointerX - 0.5) * 0.05);
  const centerY = scene.height * (0.58 + (frame.pointerY - 0.5) * 0.06);
  drawBeams(context, scene, frame);
  drawParticles(context, scene, frame, centerX, centerY);
  drawRadar(context, scene, frame, centerX, centerY);

  const vignette = context.createRadialGradient(
    scene.width * 0.35,
    scene.height * 0.5,
    40,
    scene.width * 0.45,
    scene.height * 0.55,
    Math.max(scene.width, scene.height) * 0.85,
  );
  vignette.addColorStop(0, 'rgba(4, 16, 24, 0)');
  vignette.addColorStop(1, 'rgba(4, 16, 24, 0.35)');
  context.fillStyle = vignette;
  context.fillRect(0, 0, scene.width, scene.height);
}
```

- [ ] **Step 4: Run focused and TypeScript checks**

Run:

```powershell
npm run test:login-radar
npm run typecheck
```

Expected: `PASS login-radar-canvas-contract`; TypeScript exits `0` without diagnostics.

- [ ] **Step 5: Commit the renderer**

```powershell
git add frontend/scripts/brand/login-radar-canvas-contract.mjs frontend/src/visuals/loginRadarCanvas.ts frontend/package.json
git diff --cached --check
git commit -m "feat: restore deterministic login radar renderer"
```

Expected: one commit containing only the three listed files.

---

### Task 2: Observable Login Integration and Complete Scan Composition

**Files:**
- Modify: `frontend/scripts/brand/login-radar-canvas-contract.mjs`
- Modify: `frontend/scripts/login-page-contract.mjs:38-66`
- Modify: `frontend/src/components/LoginStageCanvas.tsx`
- Modify: `frontend/src/pages/Login.tsx:246-251`
- Modify: `frontend/src/index.css:20-120, 680-820`
- Delete: `frontend/scripts/brand/behavior-space-canvas-contract.mjs`
- Delete: `frontend/src/brand/behaviorFieldCanvas.ts`

**Interfaces:**
- Consumes: `createLoginRadarScene`, `drawLoginRadarFrame`, and `LoginRadarScene` from Task 1.
- Produces: `data-login-visual="radar"` on the canvas.
- Produces: `data-login-visual-layer="scan"` on the separate scan-light layer.
- Preserves: `data-brand-visual-state` and `[login.brand-visual]` failure telemetry.

- [ ] **Step 1: Expand the focused source contract before integration**

Replace `frontend/scripts/brand/login-radar-canvas-contract.mjs` with:

```js
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const rendererPath = resolve(root, 'src/visuals/loginRadarCanvas.ts');
const oldRendererPath = resolve(root, 'src/brand/behaviorFieldCanvas.ts');

if (!existsSync(rendererPath)) {
  throw new Error('Login radar renderer missing: src/visuals/loginRadarCanvas.ts');
}

const read = (relative) => readFileSync(resolve(root, relative), 'utf8');
const renderer = read('src/visuals/loginRadarCanvas.ts');
const canvas = read('src/components/LoginStageCanvas.tsx');
const login = read('src/pages/Login.tsx');
const css = read('src/index.css');

for (const token of [
  'createSeededRandom',
  'createLoginRadarScene',
  'drawLoginRadarFrame',
  'drawGrid',
  'drawRadar',
  'drawParticles',
  'drawBeams',
  'PulseRing',
]) {
  if (!renderer.includes(token)) throw new Error(`Login radar renderer contract missing: ${token}`);
}
if (renderer.includes('Math.random')) {
  throw new Error('Login radar renderer must be deterministic; Math.random is forbidden');
}

for (const token of [
  'createLoginRadarScene',
  'drawLoginRadarFrame',
  'data-login-visual="radar"',
  'data-brand-visual-state',
  '[login.brand-visual]',
  'Login radar canvas failed',
]) {
  if (!canvas.includes(token)) throw new Error(`Observable radar canvas integration missing: ${token}`);
}

for (const token of ['login-stage-scan', 'data-login-visual-layer="scan"']) {
  if (!login.includes(token)) throw new Error(`Login scan-light DOM missing: ${token}`);
}
for (const token of ['@keyframes scan-sweep', '.login-stage-scan', 'animation: scan-sweep']) {
  if (!css.includes(token)) throw new Error(`Login scan-light CSS missing: ${token}`);
}
if (!/@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.login-stage-scan,[\s\S]*?animation: none !important;/.test(css)) {
  throw new Error('Reduced-motion scan-light suppression missing');
}
if (canvas.includes('drawBehaviorFieldFrame') || canvas.includes('behaviorFieldCanvas')) {
  throw new Error('Obsolete perspective-plane import remains in LoginStageCanvas');
}
if (existsSync(oldRendererPath)) {
  throw new Error('Obsolete perspective-plane renderer still exists');
}

console.log('PASS login-radar-canvas-contract');
```

- [ ] **Step 2: Extend the browser contract before restoring the DOM and CSS**

In `assertCurrentCopy` inside `frontend/scripts/login-page-contract.mjs`, after the governed mark assertion, add:

```js
  await expectCount(page.locator('[data-login-visual="radar"]'), 1, context);
  const scanLayer = page.locator('[data-login-visual-layer="scan"]');
  await expectCount(scanLayer, 1, context);
  const scanStyle = await scanLayer.evaluate((element) => {
    const style = getComputedStyle(element);
    return { display: style.display, animationName: style.animationName };
  });
  if (context.viewport.width <= 900) {
    if (scanStyle.display !== 'none') fail('Mobile scan-light layer must be hidden', { ...context, scanStyle });
  } else if (scanStyle.animationName !== 'scan-sweep') {
    fail('Desktop scan-light animation is missing', { ...context, scanStyle });
  }
```

In the reduced-motion context, immediately after waiting for
`[data-brand-visual-state="reduced-motion"]`, add:

```js
  const reducedScanAnimation = await reducedPage
    .locator('[data-login-visual-layer="scan"]')
    .evaluate((element) => getComputedStyle(element).animationName);
  if (reducedScanAnimation !== 'none') {
    fail('Reduced-motion scan-light animation must be disabled', {
      mode: 'reduced-motion',
      reducedScanAnimation,
    });
  }
```

- [ ] **Step 3: Run both contracts and verify the intended red state**

Run:

```powershell
npm run test:login-radar
npm run build
$node = (Get-Command node).Source
$vite = (Resolve-Path 'node_modules/vite/bin/vite.js').Path
$preview = Start-Process -FilePath $node `
  -ArgumentList @($vite, 'preview', '--host', '127.0.0.1', '--port', '4173', '--strictPort') `
  -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru
$previewReady = $false
$lastPreviewError = $null
for ($attempt = 0; $attempt -lt 30; $attempt += 1) {
  Start-Sleep -Milliseconds 200
  if ($preview.HasExited) { throw "Baseline preview exited with $($preview.ExitCode)" }
  try {
    $ready = Invoke-WebRequest -Uri 'http://127.0.0.1:4173/favicon.svg' -UseBasicParsing -TimeoutSec 2
    if ($ready.StatusCode -eq 200) { $previewReady = $true; break }
  } catch { $lastPreviewError = $_.Exception.Message }
}
if (-not $previewReady) {
  Stop-Process -Id $preview.Id -Force
  throw "Baseline preview did not become ready: $lastPreviewError"
}
$env:LOGIN_PAGE_URL = 'http://127.0.0.1:4173/login?next=%2Fdashboard'
npm run test:login-contract
$running = Get-CimInstance Win32_Process -Filter "ProcessId = $($preview.Id)"
if (-not $running -or $running.CommandLine -notlike "*$vite*" -or $running.CommandLine -notlike '* preview *') {
  throw 'Refusing to stop an unverified preview process'
}
Stop-Process -Id $preview.Id -Force
```

Expected:

- source contract fails with `Observable radar canvas integration missing: createLoginRadarScene`;
- browser contract fails because `[data-login-visual="radar"]` or `[data-login-visual-layer="scan"]` is absent.

After observing both failures, confirm
`Get-NetTCPConnection -LocalPort 4173 -State Listen` returns no listener.

- [ ] **Step 4: Replace the canvas lifecycle integration**

Replace `frontend/src/components/LoginStageCanvas.tsx` with:

```tsx
import { useEffect, useRef, useState } from 'react';
import {
  createLoginRadarScene,
  drawLoginRadarFrame,
  type LoginRadarScene,
} from '../visuals/loginRadarCanvas';

type LoginStageCanvasProps = {
  pointerX: number;
  pointerY: number;
  focusBoost?: boolean;
};

type BrandVisualState = 'initializing' | 'ready' | 'reduced-motion' | 'failed';

export function LoginStageCanvas({
  pointerX,
  pointerY,
  focusBoost = false,
}: LoginStageCanvasProps) {
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
    let animationFrame = 0;
    let scene: LoginRadarScene | null = null;
    let boost = 0;
    let publishedState: BrandVisualState = 'initializing';

    const publish = (state: BrandVisualState) => {
      if (publishedState === state || stopped) return;
      publishedState = state;
      setVisualState(state);
    };

    const fail = (
      operation: 'initialize' | 'resize' | 'render',
      reason: string,
      error?: unknown,
    ) => {
      console.error('[login.brand-visual] Login radar canvas failed', {
        operation,
        reason,
        error,
      });
      stopped = true;
      window.cancelAnimationFrame(animationFrame);
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
      try {
        const parent = canvas.parentElement;
        if (!parent) {
          fail('resize', 'parent_element_unavailable');
          return false;
        }
        const rect = parent.getBoundingClientRect();
        const width = Math.max(1, Math.floor(rect.width));
        const height = Math.max(1, Math.floor(rect.height));
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        scene = createLoginRadarScene(width, height);
        return true;
      } catch (error) {
        fail('resize', 'scene_initialization_failed', error);
        return false;
      }
    };

    const render = (time: number) => {
      if (stopped) return;
      if (!scene) {
        fail('render', 'scene_unavailable');
        return;
      }
      boost += ((focusRef.current ? 1 : 0) - boost) * 0.06;
      try {
        drawLoginRadarFrame(context, scene, {
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
      if (!reducedMotion) animationFrame = window.requestAnimationFrame(render);
    };

    const restart = () => {
      window.cancelAnimationFrame(animationFrame);
      if (!resize() || stopped) return;
      if (reducedMotion) render(0);
      else animationFrame = window.requestAnimationFrame(render);
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
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener('resize', restart);
      media.removeEventListener?.('change', onMotionChange);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="login-stage-canvas"
      aria-hidden="true"
      data-login-visual="radar"
      data-brand-visual-state={visualState}
    />
  );
}
```

Immediately run:

```powershell
npm run typecheck
```

Expected: exit `0`.

- [ ] **Step 5: Restore the separate scan-light DOM layer**

In `frontend/src/pages/Login.tsx`, immediately after `login-stage-glow`, add:

```tsx
      <div
        className="login-stage-scan"
        data-login-visual-layer="scan"
        aria-hidden="true"
      />
```

Immediately run:

```powershell
npm run typecheck
```

Expected: exit `0`.

- [ ] **Step 6: Restore scan-light CSS with reduced-motion and mobile handling**

Near the global login keyframes in `frontend/src/index.css`, add:

```css
@keyframes scan-sweep {
  0% { transform: translateY(-140%); opacity: 0; }
  8% { opacity: 1; }
  45% { opacity: .55; }
  100% { transform: translateY(260%); opacity: 0; }
}
```

Add `.login-stage-scan` to the zero-layer selector:

```css
.login-page > .login-stage-canvas,
.login-page > .login-stage-glow,
.login-page > .login-stage-scan,
.login-page > .login-stage-orb,
.login-page > .login-light-bleed,
.login-page > .login-hud-frame {
  z-index: 0;
}
```

After `.login-stage-glow`, add:

```css
.login-stage-scan {
  position: absolute;
  left: 0;
  right: 42%;
  top: 0;
  height: 18%;
  background: linear-gradient(180deg, transparent, rgba(45,212,191,.14), transparent);
  pointer-events: none;
  animation: scan-sweep 5.5s linear infinite;
  z-index: 0;
  mix-blend-mode: screen;
  opacity: .22;
}
```

Add `.login-stage-scan` to the existing reduced-motion animation suppression list:

```css
@media (prefers-reduced-motion: reduce) {
  .login-stage-scan,
  .login-title-accent,
  .login-health-dot,
  .login-submit,
  .login-submit::after,
  .login-hud-frame i,
  .login-stage-orb,
  .login-value-list li,
  .login-panel-inner {
    animation: none !important;
  }
}
```

At the existing `@media (max-width: 900px)` hiding rule, include the scan layer:

```css
  .login-light-bleed,
  .login-hud-frame,
  .login-stage-orb,
  .login-stage-scan { display: none; }
```

Immediately run:

```powershell
npm run build
```

Expected: the CSS parses and the production build exits `0`.

- [ ] **Step 7: Remove the obsolete perspective-plane implementation**

Delete:

```text
frontend/src/brand/behaviorFieldCanvas.ts
frontend/scripts/brand/behavior-space-canvas-contract.mjs
```

Use `apply_patch` deletions. Do not use recursive or wildcard filesystem deletion.

- [ ] **Step 8: Run focused, browser-source, type, and lint checks**

Run:

```powershell
npm run test:login-radar
npm run typecheck
npm run lint
```

Expected:

- `PASS login-radar-canvas-contract`;
- TypeScript exits `0`;
- ESLint exits `0` with zero warnings.

Build and run the isolated preview on test-only port `4173`, then run:

```powershell
$env:LOGIN_PAGE_URL = 'http://127.0.0.1:4173/login?next=%2Fdashboard'
npm run test:login-contract
```

Expected: `PASS login-page-contract: 3 modes × 2 viewports + copy/favicon/motion/failure`.

- [ ] **Step 9: Commit the integration**

```powershell
git add frontend/scripts/brand/login-radar-canvas-contract.mjs frontend/scripts/brand/behavior-space-canvas-contract.mjs frontend/scripts/login-page-contract.mjs frontend/src/components/LoginStageCanvas.tsx frontend/src/pages/Login.tsx frontend/src/index.css frontend/src/brand/behaviorFieldCanvas.ts
git diff --cached --check
git commit -m "feat: restore complete login radar composition"
```

Expected: one commit containing only the listed integration, contract, CSS, and deletion paths.

---

### Task 3: Living Brand Contract

**Files:**
- Modify: `frontend/scripts/brand/login-radar-canvas-contract.mjs`
- Modify: `AGENTS.md:87-94`

**Interfaces:**
- Consumes: the approved semantics from `docs/superpowers/specs/2026-07-12-login-radar-restoration-design.md`.
- Produces: one authoritative repository rule that permits the login radar only as decorative behavior observation.

- [ ] **Step 1: Add the living-document assertion before changing the document**

At the end of `frontend/scripts/brand/login-radar-canvas-contract.mjs`, before the success log, add:

```js
const agents = read('../AGENTS.md');
for (const token of [
  'login radar is an approved decorative metaphor for enterprise-system behavior observation',
  'insect, crawler, spider-web, and scraping semantics remain prohibited',
  'Decorative brand motion must never represent actual system health',
]) {
  if (!agents.includes(token)) throw new Error(`Living radar brand contract missing: ${token}`);
}
```

- [ ] **Step 2: Run the contract and verify the intended red state**

Run:

```powershell
cd frontend
npm run test:login-radar
```

Expected: non-zero exit containing `Living radar brand contract missing`.

- [ ] **Step 3: Replace the conflicting brand bullets in `AGENTS.md`**

Keep the existing Behavior Field positioning and replace only the radar prohibition and motion bullets with:

```markdown
- The login radar is an approved decorative metaphor for enterprise-system behavior observation; it is not a product-health signal and is not part of the governed logo geometry.
- Brand and decorative product visuals use no insect, crawler, spider-web, or scraping semantics. `Bug` means a verified divergence between observed and expected behavior.
- Decorative brand motion must never represent actual system health, provider health, campaign health, scan health, model health, evaluator health, or commercial readiness.
```

Do not change the existing industry-neutral, copy-preservation, or `5174`/`8088` bullet.

- [ ] **Step 4: Run focused and documentation checks**

Run:

```powershell
npm run test:login-radar
cd ..
git diff --check -- AGENTS.md frontend/scripts/brand/login-radar-canvas-contract.mjs
```

Expected: focused contract passes; diff check exits `0`.

- [ ] **Step 5: Commit the living contract**

```powershell
git add AGENTS.md frontend/scripts/brand/login-radar-canvas-contract.mjs
git diff --cached --check
git commit -m "docs: approve login radar observation metaphor"
```

Expected: one commit containing only `AGENTS.md` and the focused contract.

---

### Task 4: Full Verification and Visual Acceptance

**Files:**
- Verify only; no production edit is expected.

**Interfaces:**
- Consumes: all Task 1–3 outputs.
- Produces: fresh command evidence, desktop/mobile visual evidence, and a clean feature worktree ready for the finishing workflow.

- [ ] **Step 1: Run the complete frontend gate from the feature worktree**

Run:

```powershell
$tasks = @(
  'test:login-radar',
  'test:brand-mark',
  'brand:check',
  'typecheck',
  'lint',
  'build'
)
foreach ($task in $tasks) {
  npm run $task
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: every command exits `0`; the existing Vite large-chunk warning may remain, but no test, type, lint, asset, or build error is allowed.

- [ ] **Step 2: Run the complete browser contract on the isolated preview**

Start the built preview on `127.0.0.1:4173` with `--strictPort`, record its PID, and run:

```powershell
$env:LOGIN_PAGE_URL = 'http://127.0.0.1:4173/login?next=%2Fdashboard'
npm run test:login-contract
```

Expected: `PASS login-page-contract: 3 modes × 2 viewports + copy/favicon/motion/failure`.

Verify the served favicon:

```powershell
$favicon = Invoke-WebRequest -Uri 'http://127.0.0.1:4173/favicon.svg' -UseBasicParsing
if ($favicon.Headers['Content-Type'] -notlike 'image/svg+xml*') { throw "wrong favicon content type: $($favicon.Headers['Content-Type'])" }
if (-not $favicon.Content.TrimStart().StartsWith('<svg')) { throw 'favicon returned non-SVG content' }
```

Expected: no exception.

- [ ] **Step 3: Inspect desktop, mobile, and reduced-motion visuals**

Use the in-app Browser skill against the isolated preview and verify:

- desktop `1280×720`: circular radar, sweep wedge, pulse rings, particles, links, beams, and scan-light are visible behind the unchanged text;
- mobile `390×844`: radar canvas remains readable, separate scan-light is hidden, and there is no horizontal overflow;
- reduced motion: one stable radar frame, no moving sweep/particles/beams/pulse rings, scan-light animation disabled;
- the Behavior Field logo remains visible and unchanged;
- the health badge reflects the actual mocked or real API response and is visually independent of the radar.

Save screenshots outside the repository or in the app session; do not add test screenshots to source control.

- [ ] **Step 4: Stop only the verified isolated preview process**

Before stopping, verify the PID command line contains this worktree's Vite path and `preview`. Stop that exact PID and confirm port `4173` is released. Do not stop existing `5174` or `8088` processes.

- [ ] **Step 5: Audit the final diff and worktree**

Run:

```powershell
git diff --check HEAD~3..HEAD
git status --short
rg -n -i "insect|crawler|spider|scraper|scrape|爬虫|蜘蛛|甲虫" frontend/src frontend/public/brand AGENTS.md
```

Expected:

- diff check exits `0`;
- feature worktree has no uncommitted files;
- semantic scan finds only explicit prohibition text or contract assertions, never production visual semantics.

- [ ] **Step 6: Enter the finishing workflow**

Invoke `superpowers:finishing-a-development-branch`. Because repository instructions prohibit subagents, conduct review inline. Present the standard local merge / PR / keep / discard choices and execute the user's selection.

If the user selects local merge:

- confirm feature files do not overlap unrelated main-worktree changes;
- merge without discarding or staging unrelated files;
- rerun Task 4 Step 1 on merged `main`;
- run the login browser contract against the actual frontend on port `5174`;
- report backend `8088` as online only if a real listener and health check succeed;
- remove only the owned `.worktrees/...` worktree after merge verification succeeds.

Expected: the chosen integration workflow completes without altering unrelated user work.
