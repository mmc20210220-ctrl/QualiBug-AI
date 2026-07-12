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

const agents = read('../AGENTS.md');
for (const token of [
  'login radar is an approved decorative metaphor for enterprise-system behavior observation',
  'insect, crawler, spider-web, and scraping semantics remain prohibited',
  'Decorative brand motion must never represent actual system health',
]) {
  if (!agents.includes(token)) throw new Error(`Living radar brand contract missing: ${token}`);
}

console.log('PASS login-radar-canvas-contract');
