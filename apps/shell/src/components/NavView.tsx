// Directions on a live map, like Google Maps: the next turn as a big arrow on top, the route and where you are on
// the map, the trip below. Leaflet (~40 KB) loads only the first time you ask for directions.
import { useEffect, useRef, useState } from 'react';
import type { CircleMarker, LayerGroup, Map as LeafletMap } from 'leaflet';
import { stopNav } from '../lib/voice';
import { useStore, type Turn } from '../state/store';
import { CloseIcon } from './icons';

type Leaflet = typeof import('leaflet');
let leafletLoad: Promise<Leaflet> | null = null;
const loadLeaflet = () =>
  (leafletLoad ??= Promise.all([import('leaflet'), import('leaflet/dist/leaflet.css')])
    .then(([mod]) => ((mod as unknown as { default?: Leaflet }).default ?? mod) as Leaflet));

// dark map tiles that match PLAG's black look (CARTO, from OpenStreetMap data)
const TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTRIB = '© OpenStreetMap contributors © CARTO';

const km = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(1).replace(/\.0$/, '')} km` : `${Math.max(10, Math.round(m / 10) * 10)} m`);
const mins = (s: number) => {
  const m = Math.max(1, Math.round(s / 60));
  return m >= 60 ? `${Math.floor(m / 60)} hr ${m % 60} min` : `${m} min`;
};

const ANGLE: Partial<Record<Turn, number>> = { left: -90, right: 90, 'slight-left': -45, 'slight-right': 45, 'sharp-left': -135, 'sharp-right': 135 };

/** The turn arrow: one path, rotated per kind of turn. */
export function TurnArrow({ turn, size = 44 }: { turn: Turn; size?: number }) {
  const common = { width: size, height: size, viewBox: '0 0 48 48', fill: 'none', stroke: 'currentColor', strokeWidth: 4,
    strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true };
  if (turn === 'arrive') {
    return <svg {...common}><path d="M24 42s12-11.5 12-21a12 12 0 1 0-24 0c0 9.5 12 21 12 21z" /><circle cx="24" cy="21" r="4" /></svg>;
  }
  if (turn === 'uturn') return <svg {...common}><path d="M16 42V18a8 8 0 0 1 16 0v12" /><path d="M26 25l6 6 6-6" /></svg>;
  if (turn === 'roundabout') {
    return <svg {...common}><circle cx="24" cy="26" r="7" /><path d="M24 42v-9" /><path d="M29 21l7-9" /><path d="M29 12h7v7" /></svg>;
  }
  return (
    <svg {...common}>
      <g transform={`rotate(${ANGLE[turn] ?? 0} 24 24)`}><path d="M24 42V8" /><path d="M14 18l10-10 10 10" /></g>
    </svg>
  );
}

export default function NavView() {
  const route = useStore((s) => s.route);
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<LeafletMap | null>(null);
  const me = useRef<CircleMarker | null>(null);
  const layer = useRef<LayerGroup | null>(null);
  const [failed, setFailed] = useState(false);
  const tripKey = route ? `${route.dest.lat},${route.dest.lng},${route.path.length}` : '';

  // draw the route once per trip
  useEffect(() => {
    const r = useStore.getState().route;
    if (!r || !box.current) return;
    let alive = true;
    loadLeaflet().then((L) => {
      if (!alive || !box.current) return;
      if (!map.current) {
        map.current = L.map(box.current, { zoomControl: false, preferCanvas: true });
        L.tileLayer(TILES, { attribution: ATTRIB, subdomains: 'abcd', maxZoom: 19, detectRetina: true }).addTo(map.current);
      }
      layer.current?.remove();
      const g = L.layerGroup().addTo(map.current);
      layer.current = g;
      const line = L.polyline(r.path, { color: '#D4F34A', weight: 6, opacity: 0.95 }).addTo(g);
      L.circleMarker([r.dest.lat, r.dest.lng], { radius: 9, color: '#050506', weight: 3, fillColor: '#FF5A4E', fillOpacity: 1 })
        .bindTooltip(r.dest.name).addTo(g);
      me.current = L.circleMarker([r.here.lat, r.here.lng], { radius: 8, color: '#ffffff', weight: 3, fillColor: '#3B82F6', fillOpacity: 1 })
        .addTo(g);
      map.current.fitBounds(line.getBounds(), { padding: [28, 28] });
    }, () => setFailed(true));
    return () => { alive = false; };
  }, [tripKey]);

  // follow you as you move
  useEffect(() => {
    if (route) me.current?.setLatLng([route.here.lat, route.here.lng]);
  }, [route?.here.lat, route?.here.lng]);

  // free the map when the trip ends (or the view goes away)
  useEffect(() => {
    if (route) return;
    map.current?.remove();
    map.current = null;
    me.current = null;
    layer.current = null;
  }, [route]);
  useEffect(() => () => { map.current?.remove(); map.current = null; }, []);

  if (!route) return null;
  const step = route.steps[route.next];
  const remaining = route.steps.slice(route.next).reduce((n, s) => n + s.distance_m, 0) || route.distance_m;
  return (
    <figure className="gen-img gen-nav">
      <div className="nav-turn" role="status" aria-live="polite">
        <span className="nav-arrow"><TurnArrow turn={step?.turn ?? 'straight'} /></span>
        <span className="nav-turn-text">
          <b className="mono">{km(route.toNext)}</b>
          <span>{step?.text || 'Continue on the route'}</span>
        </span>
      </div>
      <div ref={box} className="nav-map" />
      {failed ? <p className="gen-wait">The map couldn’t load. Check the internet connection.</p> : null}
      <figcaption>
        <span className="gen-prompt" title={route.dest.address}>
          <b>{mins(route.duration_s * (remaining / Math.max(1, route.distance_m)))}</b> · {km(remaining)} · {route.dest.name}
        </span>
        <span className="nav-by mono">{route.by}{route.traffic ? ' · live traffic' : ''}</span>
        <span className="gen-actions">
          <button onClick={() => void stopNav()} aria-label="End navigation" title="End navigation"><CloseIcon /></button>
        </span>
      </figcaption>
    </figure>
  );
}
