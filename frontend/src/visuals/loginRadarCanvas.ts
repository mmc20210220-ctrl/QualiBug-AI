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
  sweepAngle: number,
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

    // Sweep flare: particles near the radar sweep line briefly ignite,
    // like contacts lit by a passing beam. Deterministic per frame.
    const particleAngle = Math.atan2(particle.y - centerY, particle.x - centerX);
    let angularGap = Math.abs(particleAngle - (sweepAngle % (Math.PI * 2)));
    if (angularGap > Math.PI) angularGap = Math.PI * 2 - angularGap;
    const flare = frame.reducedMotion ? 0 : Math.max(0, 1 - angularGap / 0.42);

    const glow = 0.45 + Math.sin(frame.time * 0.004 + particle.pulse) * 0.3;
    const radius = particle.r * (1 + flare * 1.15);
    context.beginPath();
    context.arc(particle.x, particle.y, radius, 0, Math.PI * 2);
    context.fillStyle = `rgba(167, 243, 208, ${Math.min(1, 0.35 + glow * 0.4 + flare * 0.55)})`;
    context.shadowColor = flare > 0.25 ? 'rgba(204, 251, 241, 0.95)' : 'rgba(45, 212, 191, 0.55)';
    context.shadowBlur = 8 + flare * 14;
    context.fill();
    context.shadowBlur = 0;

    if (flare > 0.55) {
      context.beginPath();
      context.arc(particle.x, particle.y, radius + 4.5, 0, Math.PI * 2);
      context.strokeStyle = `rgba(94, 234, 212, ${(flare - 0.55) * 0.9})`;
      context.lineWidth = 1;
      context.stroke();
    }
  }
}

function drawInstrumentRing(
  context: CanvasRenderingContext2D,
  scene: LoginRadarScene,
  frame: LoginRadarFrame,
  centerX: number,
  centerY: number,
  radius: number,
): void {
  const glowMultiplier = 1 + frame.focusBoost * 0.45;
  const outer = radius * 1.1;
  context.save();

  // Outer bearing ring with degree ticks — the instrument face.
  context.beginPath();
  context.arc(centerX, centerY, outer, 0, Math.PI * 2);
  context.strokeStyle = `rgba(45, 212, 191, ${0.1 * glowMultiplier})`;
  context.lineWidth = 1;
  context.stroke();

  for (let degree = 0; degree < 360; degree += 6) {
    const major = degree % 30 === 0;
    const tickAngle = (degree * Math.PI) / 180;
    const inner = outer - (major ? 10 : 5);
    context.beginPath();
    context.moveTo(centerX + Math.cos(tickAngle) * inner, centerY + Math.sin(tickAngle) * inner);
    context.lineTo(centerX + Math.cos(tickAngle) * outer, centerY + Math.sin(tickAngle) * outer);
    context.strokeStyle = `rgba(94, 234, 212, ${(major ? 0.3 : 0.14) * glowMultiplier})`;
    context.lineWidth = major ? 1.3 : 1;
    context.stroke();
  }

  // Cardinal bearing markers (N/E/S/W style diamond pips, no text).
  for (let quarter = 0; quarter < 4; quarter += 1) {
    const pipAngle = (quarter * Math.PI) / 2;
    const pipX = centerX + Math.cos(pipAngle) * (outer + 6);
    const pipY = centerY + Math.sin(pipAngle) * (outer + 6);
    context.save();
    context.translate(pipX, pipY);
    context.rotate(pipAngle + Math.PI / 4);
    context.fillStyle = `rgba(153, 246, 228, ${0.4 * glowMultiplier})`;
    context.fillRect(-2.2, -2.2, 4.4, 4.4);
    context.restore();
  }

  // Slow counter-rotating dashed ring — mechanical instrument feel.
  context.save();
  context.translate(centerX, centerY);
  context.rotate(frame.reducedMotion ? 0.6 : -frame.time * 0.00035);
  context.setLineDash([3, 9]);
  context.beginPath();
  context.arc(0, 0, radius * 0.62, 0, Math.PI * 2);
  context.strokeStyle = `rgba(56, 189, 248, ${0.22 * glowMultiplier})`;
  context.lineWidth = 1;
  context.stroke();
  context.setLineDash([1, 6]);
  context.beginPath();
  context.arc(0, 0, radius * 0.44, 0, Math.PI * 2);
  context.strokeStyle = `rgba(94, 234, 212, ${0.16 * glowMultiplier})`;
  context.stroke();
  context.setLineDash([]);
  context.restore();

  context.restore();
}

function drawEscortSatellites(
  context: CanvasRenderingContext2D,
  frame: LoginRadarFrame,
  centerX: number,
  centerY: number,
  radius: number,
): void {
  // Two escort contacts on fixed orbits — deterministic positions.
  const orbits = [
    { ratio: 0.44, speed: 0.00062, offset: 1.1, size: 2.1 },
    { ratio: 0.8, speed: -0.00041, offset: 3.6, size: 1.7 },
  ];
  for (const orbit of orbits) {
    const angle = frame.reducedMotion ? orbit.offset : orbit.offset + frame.time * orbit.speed;
    const x = centerX + Math.cos(angle) * radius * orbit.ratio;
    const y = centerY + Math.sin(angle) * radius * orbit.ratio;
    const trailAngle = angle - 0.32 * Math.sign(orbit.speed);
    context.beginPath();
    context.moveTo(centerX + Math.cos(trailAngle) * radius * orbit.ratio, centerY + Math.sin(trailAngle) * radius * orbit.ratio);
    context.lineTo(x, y);
    context.strokeStyle = 'rgba(125, 211, 252, 0.28)';
    context.lineWidth = 1;
    context.stroke();
    context.beginPath();
    context.arc(x, y, orbit.size, 0, Math.PI * 2);
    context.fillStyle = 'rgba(186, 230, 253, 0.85)';
    context.shadowColor = 'rgba(56, 189, 248, 0.8)';
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
  radius: number,
  angle: number,
): void {
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

  // Comet sweep: layered slices form a smooth fading trail behind the beam.
  context.save();
  context.translate(centerX, centerY);
  context.rotate(angle);
  const trailSlices = 16;
  for (let slice = 0; slice < trailSlices; slice += 1) {
    const ratio = slice / trailSlices;
    const sliceStart = -0.92 + ratio * 0.92;
    const sliceEnd = sliceStart + 0.1;
    const fade = ratio * ratio;
    context.beginPath();
    context.moveTo(0, 0);
    context.arc(0, 0, radius, sliceStart, sliceEnd);
    context.closePath();
    context.fillStyle = `rgba(45, 212, 191, ${(0.02 + fade * 0.16) * glowMultiplier})`;
    context.fill();
  }
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
  // Beam tip highlight.
  context.beginPath();
  context.arc(radius, 0, 2.6, 0, Math.PI * 2);
  context.fillStyle = 'rgba(240, 253, 250, 0.95)';
  context.shadowColor = 'rgba(94, 234, 212, 1)';
  context.shadowBlur = 12;
  context.fill();
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
  // Core crosshair pip.
  context.shadowBlur = 0;
  context.beginPath();
  context.arc(centerX, centerY, 9.5, 0, Math.PI * 2);
  context.strokeStyle = `rgba(204, 251, 241, ${0.5 * glowMultiplier})`;
  context.lineWidth = 1;
  context.stroke();
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
  const radius = Math.min(scene.width, scene.height) * (0.34 + frame.focusBoost * 0.02);
  const sweepAngle = frame.reducedMotion ? -0.35 : frame.time * (0.0015 + frame.focusBoost * 0.0006);
  drawBeams(context, scene, frame);
  drawParticles(context, scene, frame, centerX, centerY, sweepAngle);
  drawInstrumentRing(context, scene, frame, centerX, centerY, radius);
  drawEscortSatellites(context, frame, centerX, centerY, radius);
  drawRadar(context, scene, frame, centerX, centerY, radius, sweepAngle);

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
