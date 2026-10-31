import * as THREE from 'three';
import { geoEquirectangular, geoPath } from 'd3-geo';
import { feature, mesh } from 'topojson-client';
import type { Topology, GeometryCollection } from 'topojson-specification';
import type { Feature, FeatureCollection, MultiLineString } from 'geojson';
import landTopo from 'world-atlas/land-110m.json';
import statesTopo from 'us-atlas/states-10m.json';

export const R = 1;

export function toVec3(lat: number, lon: number, radius = R) {
  const phi = (90 - lat) * Math.PI / 180;
  const theta = (lon + 180) * Math.PI / 180;
  return new THREE.Vector3(-radius * Math.sin(phi) * Math.cos(theta), radius * Math.cos(phi), radius * Math.sin(phi) * Math.sin(theta));
}

/** Points along the great circle from a to b, lifted into an arc proportional to distance. */
export function arcPoints(a: THREE.Vector3, b: THREE.Vector3, segments = 48, lift = 1) {
  const ua = a.clone().normalize(), ub = b.clone().normalize();
  const angle = ua.angleTo(ub);
  const height = (0.015 + 0.32 * angle / Math.PI) * lift;
  const out: THREE.Vector3[] = [];
  const sin = Math.sin(angle) || 1;
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const p = angle < 1e-6 ? ua.clone() : ua.clone().multiplyScalar(Math.sin((1 - t) * angle) / sin).add(ub.clone().multiplyScalar(Math.sin(t * angle) / sin));
    out.push(p.normalize().multiplyScalar(R * (1.002 + height * Math.sin(Math.PI * t))));
  }
  return out;
}

export function sampleArc(points: THREE.Vector3[], t: number, target: THREE.Vector3) {
  const f = Math.min(0.99999, Math.max(0, t)) * (points.length - 1);
  const i = Math.floor(f);
  return target.copy(points[i]).lerp(points[i + 1], f - i);
}

let dotCache: Float32Array | null = null;

/** Rasterise the land mask once and sample an equal-area-ish dot lattice from it. */
export function landDots(step = 0.55) {
  if (dotCache) return dotCache;
  const W = 2048, H = 1024;
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d', {willReadFrequently: true})!;
  const topo = landTopo as unknown as Topology<{land: GeometryCollection}>;
  const land = feature(topo, topo.objects.land) as unknown as FeatureCollection | Feature;
  const projection = geoEquirectangular().scale(W / (2 * Math.PI)).translate([W / 2, H / 2]);
  ctx.fillStyle = '#fff';
  ctx.beginPath();
  geoPath(projection, ctx)(land);
  ctx.fill();
  const pixels = ctx.getImageData(0, 0, W, H).data;
  const values: number[] = [];
  for (let lat = -82; lat <= 83; lat += step) {
    const count = Math.max(1, Math.round(360 / step * Math.cos(lat * Math.PI / 180)));
    for (let i = 0; i < count; i++) {
      const lon = -180 + (i + 0.5) * 360 / count;
      const x = Math.floor((lon + 180) / 360 * W), y = Math.floor((90 - lat) / 180 * H);
      if (pixels[(y * W + x) * 4 + 3] > 128) {
        const v = toVec3(lat, lon, R * 1.001);
        values.push(v.x, v.y, v.z);
      }
    }
  }
  dotCache = new Float32Array(values);
  return dotCache;
}

let borderCache: Float32Array | null = null;

export function stateBorders() {
  if (borderCache) return borderCache;
  const topo = statesTopo as unknown as Topology<{states: GeometryCollection}>;
  const lines = mesh(topo, topo.objects.states) as MultiLineString;
  const values: number[] = [];
  for (const line of lines.coordinates) {
    for (let i = 0; i < line.length - 1; i++) {
      const a = toVec3(line[i][1], line[i][0], R * 1.0015), b = toVec3(line[i + 1][1], line[i + 1][0], R * 1.0015);
      values.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
  }
  borderCache = new Float32Array(values);
  return borderCache;
}

const CALM = new THREE.Color('#38e1d0'), STORM = new THREE.Color('#ffb547'), SEVERE = new THREE.Color('#ff5d7a');

/** Colour by the share of records whose weather delay exceeded 15 minutes. */
export function weatherColor(share: number) {
  const s = Math.min(1, Math.max(0, share / 0.35));
  return s < 0.5 ? CALM.clone().lerp(STORM, s * 2) : STORM.clone().lerp(SEVERE, (s - 0.5) * 2);
}

let dotTexture: THREE.Texture | null = null;
export function roundDot() {
  if (dotTexture) return dotTexture;
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d')!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.55, 'rgba(255,255,255,0.95)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.beginPath(); g.arc(32, 32, 32, 0, Math.PI * 2); g.fill();
  dotTexture = new THREE.CanvasTexture(c);
  return dotTexture;
}
