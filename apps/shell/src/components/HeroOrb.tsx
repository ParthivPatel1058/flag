// The hero sphere. Geometry comes from the thinking-orbs engine (libraries.dev); PLAG paints it with an
// iridescent palette that shifts with state, and the orb swells with your voice and with PLAG's own.
import { useEffect, useRef } from 'react';
import { MODE_FRAMES, resolvePreset, scaleCounts, scaleRadii } from 'thinking-orbs';
import type { ModeKey, ModeOpts, OrbFrame, OrbState } from 'thinking-orbs';
import { live } from '../lib/live';
import { selectStatus, useStore, type Status } from '../state/store';

type RGB = [number, number, number];

const PALETTES = {
  idle: ['#F4F5EE', '#93A9FF', '#DDB0FF', '#93F0CB'],
  listen: ['#D6F24B', '#8FF0C4', '#D6F24B', '#F4FFB8'],
  think: ['#7C93FF', '#B77BFF', '#FF86BF', '#8FF0C4'],
  exec: ['#D6F24B', '#7C93FF', '#8FF0C4', '#D6F24B'],
  speak: ['#D6F24B', '#EEEFE8', '#8FF0C4', '#EEEFE8'],
  halt: ['#FF5A4E', '#FF8C72', '#FF5A4E', '#C9372D'],
  dim: ['#6F726C', '#8E918A', '#6F726C', '#56594F'],
} as const;

type Look = { orb: OrbState; speed: number; palette: keyof typeof PALETTES; density: number; extra?: ModeOpts };

// Dots are tuned for a 300 pt frame; at hero size they need more presence.
const DOT_SCALE = 1.35;

export const LOOKS: Record<Status, Look> = {
  booting: { orb: 'connecting', speed: 0.55, palette: 'dim', density: 0.9 },
  idle: { orb: 'searching', speed: 0.3, palette: 'idle', density: 1.2, extra: { dimBase: 0.2, scanMul: 3.2 } },
  listening: { orb: 'listening', speed: 0.55, palette: 'listen', density: 1 },
  thinking: { orb: 'solving', speed: 0.6, palette: 'think', density: 1 },
  executing: { orb: 'working', speed: 0.7, palette: 'exec', density: 1 },
  speaking: { orb: 'composing', speed: 0.55, palette: 'speak', density: 1 },
  halted: { orb: 'breathing', speed: 0.12, palette: 'halt', density: 1 },
  offline: { orb: 'breathing', speed: 0.18, palette: 'dim', density: 0.9 },
  // camera on: a dotted ring frames the video
  watching: { orb: 'breathing', speed: 0.5, palette: 'listen', density: 1.5 },
};

// While the camera is on every state keeps the ring (so it never covers the video) and only its colour changes.
function lookFor(status: Status, cameraOn: boolean): Look {
  return cameraOn ? { ...LOOKS.watching, palette: status === 'watching' ? 'listen' : LOOKS[status].palette } : LOOKS[status];
}

// The 64 px preset shrinks counts and enlarges dots for avatar scale; undo that for hero scale.
const PRESET_64: Record<ModeKey, [count: number, size: number]> = {
  orbits: [1, 1], globe: [0.42, 1.15], rubik: [0.35, 1.05], wave: [0.341, 1], web: [1.35, 0.95],
  braid: [0.5, 1], ribbon: [0.25, 0.85], ring: [0.25, 0.956], morph: [0.702, 0.395],
};

function heroProfile(look: Look) {
  const r = resolvePreset(look.orb, 64);
  const [count, size] = PRESET_64[r.mode];
  const opts: ModeOpts = { ...scaleRadii(scaleCounts(r.opts, look.density / count), DOT_SCALE / size), ...look.extra };
  return { mode: r.mode, speed: r.speed * look.speed, opts };
}

const hex = (h: string): RGB => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const HUES = 32;
const LEVELS = 12;
const ALPHAS = 6;
const TAU = Math.PI * 2;

function samplePalette(pal: RGB[], u: number): RGB {
  const x = u * pal.length;
  const i = Math.floor(x) % pal.length;
  const j = (i + 1) % pal.length;
  let f = x - Math.floor(x);
  f = f * f * (3 - 2 * f);
  return [pal[i][0] + (pal[j][0] - pal[i][0]) * f, pal[i][1] + (pal[j][1] - pal[i][1]) * f, pal[i][2] + (pal[j][2] - pal[i][2]) * f];
}

/** Every colour a dot can take: hue x depth brightness x opacity, as ready-made rgba() strings. */
function buildLut(pal: RGB[]): string[] {
  const out: string[] = new Array(HUES * LEVELS * ALPHAS);
  for (let h = 0; h < HUES; h++) {
    const c = samplePalette(pal, h / HUES);
    for (let l = 0; l < LEVELS; l++) {
      // the library's depth ramp (near dots bright, far dots dim), lifted so the sphere glows on black
      const k = Math.min(1, 0.16 + 1.1 * Math.pow((l + 0.5) / LEVELS, 0.62));
      for (let a = 0; a < ALPHAS; a++) {
        const alpha = ((a + 1) / ALPHAS).toFixed(2);
        out[(h * LEVELS + l) * ALPHAS + a] = `rgba(${(c[0] * k) | 0},${(c[1] * k) | 0},${(c[2] * k) | 0},${alpha})`;
      }
    }
  }
  return out;
}

function colorIndex(dx: number, dy: number, z: number, R: number, rot: number, white: number, a: number): number {
  let u = Math.atan2(dy, dx) / TAU + 0.5 + rot + (z / R) * 0.08;
  u -= Math.floor(u);
  const k = 1 - Math.min(1, Math.max(0, white));
  const hue = Math.min(HUES - 1, (u * HUES) | 0);
  const lvl = Math.min(LEVELS - 1, (k * LEVELS) | 0);
  const al = Math.max(0, Math.min(ALPHAS - 1, Math.round(a * ALPHAS) - 1));
  return (hue * LEVELS + lvl) * ALPHAS + al;
}

// Dots are grouped by colour and filled as one path per colour: a few dozen fills per frame instead of thousands.
const buckets = new Map<number, number[]>();

function paint(ctx: CanvasRenderingContext2D, f: OrbFrame, S: number, lut: string[], rot: number, alpha: number) {
  const c = S / 2;
  ctx.globalAlpha = alpha;
  for (const l of f.lines) {
    ctx.strokeStyle = lut[colorIndex((l.x1 + l.x2) / 2 - c, (l.y1 + l.y2) / 2 - c, 0, c, rot, l.white, l.a ?? 1)];
    ctx.lineWidth = l.w;
    ctx.beginPath();
    ctx.moveTo(l.x1, l.y1);
    ctx.lineTo(l.x2, l.y2);
    ctx.stroke();
  }
  for (const list of buckets.values()) list.length = 0;
  const dots = f.dots;
  for (let i = 0; i < dots.length; i++) {
    const d = dots[i];
    const key = colorIndex(d.x - c, d.y - c, d.z, c, rot, d.white, d.a ?? 1);
    let list = buckets.get(key);
    if (!list) buckets.set(key, (list = []));
    list.push(i);
  }
  for (const [key, list] of buckets) {
    if (!list.length) continue;
    ctx.fillStyle = lut[key];
    ctx.beginPath();
    for (const i of list) {
      const d = dots[i];
      ctx.moveTo(d.x + d.r, d.y);
      ctx.arc(d.x, d.y, d.r, 0, TAU);
    }
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

type Layer = { mode: ModeKey; opts: ModeOpts; speed: number; t: number; alpha: number; target: number };

export default function HeroOrb() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

    let S = 0;
    let dpr = 1;
    const measure = () => {
      const r = canvas.getBoundingClientRect();
      S = Math.max(1, Math.round(r.width));
      dpr = Math.min(1.25, window.devicePixelRatio || 1); // sharp enough for dots, a third fewer pixels than 1.5
      canvas.width = Math.round(S * dpr);
      canvas.height = Math.round(S * dpr);
      live.orb = { x: r.left + r.width / 2, y: r.top + r.height / 2, r: r.width / 2 };
    };
    const ro = new ResizeObserver(measure);
    ro.observe(canvas);
    window.addEventListener('resize', measure);
    measure();

    const lookOf = (s: ReturnType<typeof useStore.getState>) => lookFor(selectStatus(s), s.cameraOn);
    let look = lookOf(useStore.getState());
    let layers: Layer[] = [];
    const addLayer = () => {
      const p = heroProfile(look);
      layers.forEach((l) => (l.target = 0));
      layers.push({ mode: p.mode, opts: p.opts, speed: p.speed, t: Math.random() * 20, alpha: layers.length ? 0 : 1, target: 1 });
    };
    addLayer();

    let targetPal = PALETTES[look.palette].map(hex);
    const pal = targetPal.map((c) => [...c] as RGB);
    let lut = buildLut(pal);

    const unsubscribe = useStore.subscribe((s) => {
      const next = lookOf(s);
      if (next.orb === look.orb && next.palette === look.palette && next.speed === look.speed) return;
      const orbChanged = next.orb !== look.orb;
      look = next;
      targetPal = PALETTES[look.palette].map(hex);
      if (orbChanged) addLayer();
      else layers[layers.length - 1].speed = heroProfile(look).speed;
    });

    let raf = 0;
    let last = performance.now();
    let rot = 0;
    let glowAt = 0;
    let level = 0;
    let remeasure = 0;
    let status = selectStatus(useStore.getState());
    const unsubscribeStatus = useStore.subscribe((s) => {
      status = selectStatus(s);
    });
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      // ~50 fps while PLAG is working or talking, ~24 fps at rest: the resting orb must stay cheap.
      const busy = status === 'listening' || status === 'thinking' || status === 'executing' || status === 'speaking';
      if (now - last < (busy ? 19 : 55)) return;  // ~52 fps busy, ~18 fps at rest (idle orb stays very cheap)
      const dt = (reduce ? 0.2 : 1) * Math.min(0.05, (now - last) / 1000);
      last = now;
      if (document.hidden || S < 2) return;
      if ((remeasure += dt) > 1) {
        remeasure = 0;
        const r = canvas.getBoundingClientRect();
        live.orb = { x: r.left + r.width / 2, y: r.top + r.height / 2, r: r.width / 2 };
      }

      // your voice, PLAG's voice, and (with the wake word on) the room: the orb is always listening
      const target = Math.max(live.mic, live.speak, live.ambient * 0.45);
      level += (target - level) * Math.min(1, dt * (target > level ? 16 : 5));

      let moving = false;
      for (let i = 0; i < pal.length; i++) {
        for (let ch = 0; ch < 3; ch++) {
          const d = targetPal[i][ch] - pal[i][ch];
          if (Math.abs(d) > 0.6) {
            pal[i][ch] += d * Math.min(1, dt * 4);
            moving = true;
          } else pal[i][ch] = targetPal[i][ch];
        }
      }
      if (moving) lut = buildLut(pal);
      // feed the aura behind the sphere the current lead colour (throttled: it's only a CSS variable)
      if ((glowAt += dt) > 0.2) {
        glowAt = 0;
        const g = pal[0];
        canvas.style.setProperty('--glow', `rgba(${g[0] | 0},${g[1] | 0},${g[2] | 0},${(0.1 + level * 0.22).toFixed(2)})`);
      }
      rot += dt * 0.035;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, S, S);
      const scale = 1 + (reduce ? 0 : level * 0.08);
      ctx.translate(S / 2, S / 2);
      ctx.scale(scale, scale);
      ctx.translate(-S / 2, -S / 2);

      const speedMul = 1 + level * 1.8;
      layers = layers.filter((l) => l.target > 0 || l.alpha > 0.01);
      for (const l of layers) {
        l.alpha += (l.target - l.alpha) * Math.min(1, dt * 3.5);
        l.t += dt * l.speed * speedMul;
        paint(ctx, MODE_FRAMES[l.mode](S, l.t, l.opts), S, lut, rot, l.alpha);
      }
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      unsubscribe();
      unsubscribeStatus();
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  return <canvas ref={canvasRef} className="orb-canvas" role="img" aria-label="PLAG" />;
}
