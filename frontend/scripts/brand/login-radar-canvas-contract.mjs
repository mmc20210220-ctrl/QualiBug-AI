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
