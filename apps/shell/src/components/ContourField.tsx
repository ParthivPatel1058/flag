// Topographic contour field around the orb. Idle: slow drift. Voice in or out: ripples travel outward.
import { useEffect, useRef } from 'react';
import { live } from '../lib/live';
import { selectStatus, useStore, type Status } from '../state/store';

const TINT: Record<Status, [number, number, number]> = {
  booting: [110, 114, 108],
  idle: [238, 239, 232],
  listening: [214, 242, 75],
  thinking: [168, 150, 255],
  executing: [150, 170, 255],
  speaking: [214, 242, 75],
  halted: [255, 90, 78],
  offline: [110, 114, 108],
  watching: [214, 242, 75],
};
const BONE: [number, number, number] = [238, 239, 232];

export default function ContourField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let W = 0;
    let H = 0;
    let dpr = 1;
    const resize = () => {
      dpr = 1; // thin faint lines behind everything: full resolution only costs frames (lag)
      W = window.innerWidth;
      H = window.innerHeight;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    };
    resize();
    window.addEventListener('resize', resize);

    let status: Status = selectStatus(useStore.getState());
    const unsubscribe = useStore.subscribe((s) => {
      status = selectStatus(s);
    });
    const tint: [number, number, number] = [...TINT[status]];

    let raf = 0;
    let last = performance.now();
    let t = 0;
    let level = 0;
    let acc = 0;
    const RINGS = 22;
    const POINTS = 120; // smooth enough for these curves, 40% less drawing per frame

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (document.hidden) return;
      const active = status === 'listening' || status === 'speaking' || status === 'thinking' || status === 'executing'
        || live.ambient > 0.08;
      acc += dt;
      if (acc < (active ? 1 / 30 : 1 / 10)) return; // ~10 fps at rest (a slow drift), ~30 fps while active
      const step = acc;
      acc = 0;
      t += step * (reduce ? 0.15 : active ? 1.2 : 0.5);
      const target = Math.max(live.mic, live.speak, live.ambient * 0.5) + (status === 'thinking' ? 0.18 : 0);
      level += (target - level) * Math.min(1, step * 6);
      const goal = TINT[status];
      for (let i = 0; i < 3; i++) tint[i] += (goal[i] - tint[i]) * Math.min(1, step * 3);

      const cx = live.orb.r ? live.orb.x : W / 2;
      const cy = live.orb.r ? live.orb.y : H * 0.42;
      const r0 = (live.orb.r || 200) * 1.08;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.lineWidth = 1;
      for (let i = 0; i < RINGS; i++) {
        const q = i / RINGS;
        const radius = r0 + i * (16 + i * 1.9);
        const mix = Math.max(0, 1 - q * 2.2); // inner rings take the state tint
        const r = BONE[0] + (tint[0] - BONE[0]) * mix;
        const g = BONE[1] + (tint[1] - BONE[1]) * mix;
        const b = BONE[2] + (tint[2] - BONE[2]) * mix;
        const alpha = 0.012 + 0.13 * Math.pow(1 - q, 1.6) * (0.75 + level * 0.6);
        ctx.strokeStyle = `rgba(${r | 0},${g | 0},${b | 0},${alpha.toFixed(3)})`;
        ctx.beginPath();
        for (let p = 0; p <= POINTS; p++) {
          const th = (p / POINTS) * Math.PI * 2;
          const c = Math.cos(th);
          const s = Math.sin(th);
          // squircle profile: rounded-rectangle contours, wider than tall
          const sx = Math.sign(c) * Math.pow(Math.abs(c), 0.72);
          const sy = Math.sign(s) * Math.pow(Math.abs(s), 0.72);
          const wob =
            1 +
            0.03 * Math.sin(3 * th + i * 0.45 + t * 0.35) +
            0.018 * Math.sin(5 * th - t * 0.5 + i * 0.21) +
            level * 0.07 * Math.sin(9 * th - t * 6 + i * 0.55) * Math.exp(-i * 0.09);
          const x = cx + sx * radius * 1.5 * wob;
          const y = cy + sy * radius * wob;
          if (p === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      unsubscribe();
      window.removeEventListener('resize', resize);
    };
  }, []);

  return <canvas ref={ref} className="contours" aria-hidden="true" />;
}
