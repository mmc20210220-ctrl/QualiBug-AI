import { deflateSync } from 'node:zlib';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const source = JSON.parse(
  readFileSync(resolve(root, 'src/brand/behavior-field-brand.json'), 'utf8'),
);
const checkOnly = process.argv.includes('--check');

function svg(detail, tone) {
  const variant = source.variants[detail];
  const palette = source.palettes[tone];
  const strokeWidth = detail === 'master' ? 13 : detail === 'compact' ? 11 : 9;
  const plane = variant.planePath
    ? `<path d="${variant.planePath}" fill="${palette.planeFill}" stroke="${palette.planeStroke}" stroke-width="${detail === 'master' ? 2.5 : 3}" stroke-linejoin="round"/>`
    : '';
  const grid = variant.gridPaths
    .map((path) => `<path d="${path}" fill="none" stroke="${palette.grid}" stroke-width="1.5" opacity=".55"/>`)
    .join('');
  const nodes = variant.nodes
    .map((node, index) => `<circle cx="${node.cx}" cy="${node.cy}" r="${node.r}" fill="${palette.nodes[index] ?? palette.nodes[0]}"/>`)
    .join('');

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${variant.viewBox}" role="img" aria-label="QualiBug AI"><defs><linearGradient id="q" x1="12%" y1="90%" x2="90%" y2="10%"><stop offset="0" stop-color="${palette.outer[0]}"/><stop offset=".55" stop-color="${palette.outer[1]}"/><stop offset="1" stop-color="${palette.outer[2]}"/></linearGradient></defs><path d="${variant.qPath}" fill="none" stroke="url(#q)" stroke-width="${strokeWidth}" stroke-linecap="round"/>${plane}${grid}<path d="${variant.trajectoryPath}" fill="none" stroke="${palette.trajectory}" stroke-width="${detail === 'micro' ? 4 : 3.5}" stroke-linecap="round"/>${nodes}</svg>\n`;
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const name = Buffer.from(type, 'ascii');
  const output = Buffer.alloc(12 + data.length);
  output.writeUInt32BE(data.length, 0);
  name.copy(output, 4);
  data.copy(output, 8);
  output.writeUInt32BE(crc32(Buffer.concat([name, data])), 8 + data.length);
  return output;
}

function microPng(size = 32) {
  const rgba = Buffer.alloc(size * size * 4);
  const setPixel = (x, y, color) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    rgba.set(color, (y * size + x) * 4);
  };
  const blue = [37, 99, 235, 255];
  const cyan = [14, 165, 233, 255];
  const teal = [45, 212, 191, 255];
  const line = (x1, y1, x2, y2, width, color) => {
    const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1) * 2);
    for (let step = 0; step <= steps; step += 1) {
      const x = x1 + ((x2 - x1) * step) / steps;
      const y = y1 + ((y2 - y1) * step) / steps;
      for (let dy = -width; dy <= width; dy += 1) {
        for (let dx = -width; dx <= width; dx += 1) {
          if (dx * dx + dy * dy <= width * width) {
            setPixel(Math.round(x + dx), Math.round(y + dy), color);
          }
        }
      }
    }
  };
  const dot = (cx, cy, radius, color) => {
    for (let y = -radius; y <= radius; y += 1) {
      for (let x = -radius; x <= radius; x += 1) {
        if (x * x + y * y <= radius * radius) setPixel(cx + x, cy + y, color);
      }
    }
  };

  const centerX = 15.5;
  const centerY = 15.5;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const dx = x - centerX;
      const dy = y - centerY;
      const radius = Math.hypot(dx, dy);
      const angle = Math.atan2(dy, dx);
      if (radius >= 10.5 && radius <= 14.5 && !(angle > 0.42 && angle < 1.08)) {
        setPixel(x, y, angle < -0.4 ? cyan : blue);
      }
    }
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

  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
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
for (const detail of ['master', 'compact', 'micro']) {
  for (const tone of ['dark', 'light', 'mono-dark', 'mono-light']) {
    outputs.set(
      `public/brand/qualibug-behavior-field-${detail}-${tone}.svg`,
      Buffer.from(svg(detail, tone)),
    );
  }
}
outputs.set('public/favicon.svg', Buffer.from(svg('micro', 'light')));
outputs.set('public/favicon.ico', ico());

const drift = [];
for (const [relative, expected] of outputs) {
  const path = resolve(root, relative);
  if (checkOnly) {
    let actual;
    try {
      actual = readFileSync(path);
    } catch {
      drift.push(`${relative}: missing`);
      continue;
    }
    if (!actual.equals(expected)) drift.push(`${relative}: stale or malformed`);
  } else {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, expected);
  }
}

if (drift.length) {
  throw new Error(`Brand asset check failed:\n${drift.join('\n')}`);
}
console.log(checkOnly ? 'PASS brand:check' : `WROTE ${outputs.size} governed brand assets`);
