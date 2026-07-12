import { useEffect, useRef, useState } from 'react';
import { drawBehaviorFieldFrame } from '../brand/behaviorFieldCanvas';

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

    const fail = (
      operation: 'initialize' | 'resize' | 'render',
      reason: string,
      error?: unknown,
    ) => {
      console.error('[login.brand-visual] Behavior Field canvas failed', {
        operation,
        reason,
        error,
      });
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

  return (
    <canvas
      ref={canvasRef}
      className="login-stage-canvas"
      aria-hidden="true"
      data-brand-visual-state={visualState}
    />
  );
}
