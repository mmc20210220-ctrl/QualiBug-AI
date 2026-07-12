import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const canvas = readFileSync(resolve(root, 'src/components/LoginStageCanvas.tsx'), 'utf8');
const renderer = readFileSync(resolve(root, 'src/brand/behaviorFieldCanvas.ts'), 'utf8');
const login = readFileSync(resolve(root, 'src/pages/Login.tsx'), 'utf8');
const css = readFileSync(resolve(root, 'src/index.css'), 'utf8');

for (const token of ['data-brand-visual-state', '[login.brand-visual]', 'drawBehaviorFieldFrame']) {
  if (!canvas.includes(token)) {
    throw new Error(`Observable canvas contract missing: ${token}`);
  }
}
for (const token of ['BEHAVIOR_NODES', 'BEHAVIOR_TRAJECTORY', 'drawPerspectivePlane']) {
  if (!renderer.includes(token)) {
    throw new Error(`Behavior-space renderer missing: ${token}`);
  }
}
for (const forbidden of ['Math.random', 'drawRadar', 'PulseRing', 'type Particle', 'type Beam', 'linkDist']) {
  if (canvas.includes(forbidden) || renderer.includes(forbidden)) {
    throw new Error(`Crawler/radar visual remains: ${forbidden}`);
  }
}
if (login.includes('login-stage-scan')) throw new Error('Scan sweep DOM remains');
if (css.includes('@keyframes scan-sweep') || css.includes('.login-stage-scan')) {
  throw new Error('Scan sweep CSS remains');
}

console.log('PASS behavior-space-canvas-contract');
