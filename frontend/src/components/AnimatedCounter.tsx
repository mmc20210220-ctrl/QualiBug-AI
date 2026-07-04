import { useState, useEffect, useRef } from 'react';

interface AnimatedCounterProps {
  value: number | string;
  duration?: number;
  className?: string;
  style?: React.CSSProperties;
  formatter?: (v: number) => string;
}

export function AnimatedCounter({ value, duration = 800, className, style, formatter }: AnimatedCounterProps) {
  const [display, setDisplay] = useState(0);
  const prevValue = useRef(0);
  const raf = useRef<number>(0);

  useEffect(() => {
    const target = typeof value === 'string' ? parseFloat(value) : value;
    cancelAnimationFrame(raf.current);
    if (isNaN(target)) {
      prevValue.current = 0;
      raf.current = requestAnimationFrame(() => {
        setDisplay(0);
      });
      return () => cancelAnimationFrame(raf.current);
    }

    const start = prevValue.current;
    const startTime = performance.now();

    const animate = (now: number) => {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      // Ease out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = start + (target - start) * eased;
      setDisplay(Math.round(current));
      if (progress < 1) {
        raf.current = requestAnimationFrame(animate);
      } else {
        prevValue.current = target;
      }
    };

    raf.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(raf.current);
  }, [value, duration]);

  const formatted = formatter ? formatter(display) : display.toString();

  return (
    <span className={className} style={style}>{formatted}</span>
  );
}
