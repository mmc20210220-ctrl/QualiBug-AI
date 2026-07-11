import { useEffect, useRef } from 'react';

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  pulse: number;
};

type Beam = {
  x: number;
  y: number;
  len: number;
  speed: number;
  angle: number;
  alpha: number;
};

type PulseRing = {
  radius: number;
  max: number;
  alpha: number;
};

type LoginStageCanvasProps = {
  pointerX: number;
  pointerY: number;
  focusBoost?: boolean;
};

export function LoginStageCanvas({ pointerX, pointerY, focusBoost = false }: LoginStageCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pointerRef = useRef({ x: 0.28, y: 0.48 });
  const focusRef = useRef(false);

  useEffect(() => {
    pointerRef.current = { x: pointerX, y: pointerY };
  }, [pointerX, pointerY]);

  useEffect(() => {
    focusRef.current = focusBoost;
  }, [focusBoost]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let reduced = media.matches;

    let width = 0;
    let height = 0;
    let dpr = 1;
    let raf = 0;
    let particles: Particle[] = [];
    let beams: Beam[] = [];
    let rings: PulseRing[] = [];
    let lastRingAt = 0;
    let boost = 0;

    const seedParticles = () => {
      const count = Math.max(36, Math.min(68, Math.floor((width * height) / 14000)));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.55,
        vy: (Math.random() - 0.5) * 0.55,
        r: 1.2 + Math.random() * 2.0,
        pulse: Math.random() * Math.PI * 2,
      }));
      beams = Array.from({ length: 8 }, () => ({
        x: Math.random() * width * 0.7,
        y: Math.random() * height,
        len: 40 + Math.random() * 90,
        speed: 1.4 + Math.random() * 2.0,
        angle: -0.4 - Math.random() * 0.4,
        alpha: 0.18 + Math.random() * 0.28,
      }));
    };

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const rect = parent.getBoundingClientRect();
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seedParticles();
      rings = [];
      lastRingAt = 0;
    };

    const drawGrid = (t: number) => {
      const drift = (t * 0.018) % 48;
      ctx.save();
      ctx.strokeStyle = `rgba(45, 212, 191, ${0.06 + boost * 0.04})`;
      ctx.lineWidth = 1;
      for (let x = -48 + drift; x <= width + 48; x += 48) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = -48 + drift * 0.65; y <= height + 48; y += 48) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }
      ctx.restore();
    };

    const drawRadar = (cx: number, cy: number, t: number) => {
      const radius = Math.min(width, height) * (0.34 + boost * 0.02);
      const glowMul = 1 + boost * 0.45;
      ctx.save();

      for (let i = 1; i <= 4; i += 1) {
        ctx.beginPath();
        ctx.arc(cx, cy, radius * (0.26 + i * 0.18), 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(45, 212, 191, ${(0.12 + i * 0.04) * glowMul})`;
        ctx.lineWidth = i === 4 ? 1.6 : 1;
        ctx.stroke();
      }

      ctx.strokeStyle = `rgba(94, 234, 212, ${0.16 * glowMul})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx - radius, cy);
      ctx.lineTo(cx + radius, cy);
      ctx.moveTo(cx, cy - radius);
      ctx.lineTo(cx, cy + radius);
      ctx.stroke();

      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      core.addColorStop(0, `rgba(45, 212, 191, ${0.22 * glowMul})`);
      core.addColorStop(0.4, `rgba(14, 165, 233, ${0.1 * glowMul})`);
      core.addColorStop(1, 'rgba(45, 212, 191, 0)');
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();

      if (!reduced) {
        const angle = t * (0.0015 + boost * 0.0006);
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(angle);
        const wedge = ctx.createLinearGradient(0, 0, radius, 0);
        wedge.addColorStop(0, `rgba(94, 234, 212, ${0.5 + boost * 0.2})`);
        wedge.addColorStop(0.35, `rgba(56, 189, 248, ${0.18 + boost * 0.1})`);
        wedge.addColorStop(1, 'rgba(45, 212, 191, 0)');
        ctx.fillStyle = wedge;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.arc(0, 0, radius, -0.55, 0.12);
        ctx.closePath();
        ctx.fill();

        ctx.strokeStyle = 'rgba(204, 251, 241, 0.85)';
        ctx.lineWidth = 1.8;
        ctx.shadowColor = 'rgba(45, 212, 191, 0.7)';
        ctx.shadowBlur = 10 + boost * 8;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(radius, 0);
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.restore();

        for (let i = 0; i < 5; i += 1) {
          const a = angle * 0.7 + i * 1.15;
          const dist = radius * (0.3 + ((i * 41) % 55) / 100);
          const bx = cx + Math.cos(a) * dist;
          const by = cy + Math.sin(a) * dist;
          const blink = 0.35 + Math.abs(Math.sin(t * 0.005 + i)) * 0.5;
          ctx.beginPath();
          ctx.arc(bx, by, 2.4, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(94, 234, 212, ${blink})`;
          ctx.shadowColor = 'rgba(45, 212, 191, 0.8)';
          ctx.shadowBlur = 8;
          ctx.fill();
          ctx.shadowBlur = 0;
        }
      }

      const ringGap = focusRef.current ? 1100 : 1600;
      if (!reduced && t - lastRingAt > ringGap) {
        rings.push({ radius: 8, max: radius * 1.15, alpha: 0.45 + boost * 0.2 });
        lastRingAt = t;
      }
      rings = rings.filter((ring) => ring.alpha > 0.02);
      for (const ring of rings) {
        if (!reduced) {
          ring.radius += 1.6 + boost * 0.6;
          ring.alpha *= 0.985;
        }
        ctx.beginPath();
        ctx.arc(cx, cy, ring.radius, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(45, 212, 191, ${ring.alpha})`;
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }

      ctx.beginPath();
      ctx.arc(cx, cy, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = '#99f6e4';
      ctx.shadowColor = 'rgba(94, 234, 212, 0.9)';
      ctx.shadowBlur = 16 + boost * 10;
      ctx.fill();
      ctx.restore();
    };

    const drawBeams = () => {
      for (const beam of beams) {
        if (!reduced) {
          beam.x += Math.cos(beam.angle) * beam.speed * (1 + boost * 0.25);
          beam.y += Math.sin(beam.angle) * beam.speed * (1 + boost * 0.25);
          if (beam.x < -120 || beam.y < -120 || beam.x > width + 120 || beam.y > height + 120) {
            beam.x = Math.random() * width * 0.7;
            beam.y = height + 40;
            beam.len = 40 + Math.random() * 90;
            beam.speed = 1.4 + Math.random() * 2.0;
            beam.alpha = 0.18 + Math.random() * 0.28;
          }
        }
        const ex = beam.x + Math.cos(beam.angle) * beam.len;
        const ey = beam.y + Math.sin(beam.angle) * beam.len;
        const grad = ctx.createLinearGradient(beam.x, beam.y, ex, ey);
        grad.addColorStop(0, `rgba(45, 212, 191, 0)`);
        grad.addColorStop(0.5, `rgba(94, 234, 212, ${beam.alpha * (1 + boost * 0.35)})`);
        grad.addColorStop(1, `rgba(186, 230, 253, 0)`);
        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.moveTo(beam.x, beam.y);
        ctx.lineTo(ex, ey);
        ctx.stroke();
      }
    };

    const drawParticles = (t: number, cx: number, cy: number) => {
      const linkDist = Math.min(150, Math.max(100, width * 0.11));
      for (let i = 0; i < particles.length; i += 1) {
        const p = particles[i];
        if (!reduced) {
          p.vx += (cx - p.x) * (0.000012 + boost * 0.00001);
          p.vy += (cy - p.y) * (0.000012 + boost * 0.00001);
          p.x += p.vx;
          p.y += p.vy;
          if (p.x < -20) p.x = width + 20;
          if (p.x > width + 20) p.x = -20;
          if (p.y < -20) p.y = height + 20;
          if (p.y > height + 20) p.y = -20;
        }

        for (let j = i + 1; j < particles.length; j += 1) {
          const q = particles[j];
          const dx = p.x - q.x;
          const dy = p.y - q.y;
          const dist = Math.hypot(dx, dy);
          if (dist < linkDist) {
            const alpha = (1 - dist / linkDist) * (0.28 + boost * 0.12);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
            ctx.strokeStyle = `rgba(94, 234, 212, ${alpha})`;
            ctx.lineWidth = 1;
            ctx.stroke();
          }
        }

        const glow = 0.45 + Math.sin(t * 0.004 + p.pulse) * 0.3;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(167, 243, 208, ${0.35 + glow * 0.4})`;
        ctx.shadowColor = 'rgba(45, 212, 191, 0.55)';
        ctx.shadowBlur = 8;
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    };

    const draw = (t: number) => {
      boost += ((focusRef.current ? 1 : 0) - boost) * 0.06;
      ctx.clearRect(0, 0, width, height);
      drawGrid(t);

      const px = pointerRef.current.x;
      const py = pointerRef.current.y;
      const cx = width * (0.22 + (px - 0.5) * 0.05);
      const cy = height * (0.58 + (py - 0.5) * 0.06);

      drawBeams();
      drawParticles(t, cx, cy);
      drawRadar(cx, cy, t);

      const vignette = ctx.createRadialGradient(width * 0.35, height * 0.5, 40, width * 0.45, height * 0.55, Math.max(width, height) * 0.85);
      vignette.addColorStop(0, 'rgba(4, 16, 24, 0)');
      vignette.addColorStop(1, 'rgba(4, 16, 24, 0.35)');
      ctx.fillStyle = vignette;
      ctx.fillRect(0, 0, width, height);

      if (!reduced) raf = window.requestAnimationFrame(draw);
    };

    const onMotionChange = () => {
      const nextReduced = media.matches;
      if (nextReduced === reduced) return;
      reduced = nextReduced;
      window.cancelAnimationFrame(raf);
      raf = 0;
      if (reduced) draw(0);
      else raf = window.requestAnimationFrame(draw);
    };

    const onResize = () => {
      resize();
      if (reduced) draw(0);
    };

    resize();
    if (reduced) draw(0);
    else raf = window.requestAnimationFrame(draw);
    window.addEventListener('resize', onResize);
    media.addEventListener?.('change', onMotionChange);

    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      media.removeEventListener?.('change', onMotionChange);
    };
  }, []);

  return <canvas ref={canvasRef} className="login-stage-canvas" aria-hidden="true" />;
}
