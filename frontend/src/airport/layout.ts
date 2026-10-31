import * as THREE from 'three';
import type { Scenario } from '../types';
import { bearing } from '../lib/format';
import { APPROACH, stateAt, type FlightState, type Schedule } from '../lib/sim';

const V = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);

export const TAXI_Z = -7;
export const HOLD_CENTER = V(-86, 0, -34);
export const HOLD_RADIUS = 10;
const HOLD_RATE = 0.85;

export type RunwayLayout = {id: string; z: number; x0: number; x1: number; touchdown: number; exit: number; entry: number};
export type StandLayout = {id: string; pos: THREE.Vector3; leadIn: THREE.Vector3; remote: boolean};

export type Layout = {
  runways: RunwayLayout[];
  stands: StandLayout[];
  pads: THREE.Vector3[];
  tower: THREE.Vector3;
  depot: THREE.Vector3;
  terminal: {x0: number; x1: number; z: number};
  inbound: Map<string, THREE.Vector3>;
  slot: Map<string, number>;
};

export function buildLayout(scenario: Scenario): Layout {
  const runways = scenario.resources.runways.map((r, i) => ({id: r.id, z: -i * 16, x0: -42, x1: 42, touchdown: -34, exit: 6, entry: -38}));
  const contact = scenario.resources.stands.filter(s => !/^R/i.test(s.id));
  const remote = scenario.resources.stands.filter(s => /^R/i.test(s.id));
  const stands: StandLayout[] = [
    ...contact.map((s, i) => ({id: s.id, pos: V(-12 + i * 12, 0, -18), leadIn: V(-12 + i * 12, 0, -11.5), remote: false})),
    ...remote.map((s, i) => ({id: s.id, pos: V(36 + i * 12, 0, -21), leadIn: V(36 + i * 12, 0, -12), remote: true})),
  ];
  const padCount = Math.max(2, Number((scenario.resources as {surface_holding_capacity?: number}).surface_holding_capacity ?? 2));
  const pads = Array.from({length: padCount}, (_, i) => V(-28 + i * 7.5, 0, -12));
  const lastContact = contact.length ? -12 + (contact.length - 1) * 12 : 0;
  const airport = scenario.airport as {latitude?: number; longitude?: number};
  const inbound = new Map<string, THREE.Vector3>();
  scenario.flights.forEach((f, i) => {
    const o = f.origin as {latitude?: number; longitude?: number};
    const deg = airport.latitude != null && o.latitude != null ? bearing(airport.latitude, airport.longitude!, o.latitude, o.longitude!) : 300 + i * 25;
    const rad = deg * Math.PI / 180;
    inbound.set(f.id, V(Math.sin(rad), 0, -Math.cos(rad)));
  });
  return {runways, stands, pads, tower: V(-40, 0, -24), depot: V(Math.max(20, lastContact + 16), 0, -30),
    terminal: {x0: -20, x1: Math.max(20, lastContact + 8), z: -28}, inbound, slot: new Map(scenario.flights.map((f, i) => [f.id, i]))};
}

function along(points: THREE.Vector3[], f: number, out: THREE.Vector3) {
  if (points.length === 1) return out.copy(points[0]);
  const lengths = points.slice(1).map((p, i) => p.distanceTo(points[i]));
  const total = lengths.reduce((a, b) => a + b, 0) || 1;
  let d = Math.min(1, Math.max(0, f)) * total;
  for (let i = 0; i < lengths.length; i++) {
    if (d <= lengths[i] || i === lengths.length - 1) return out.copy(points[i]).lerp(points[i + 1], lengths[i] ? Math.min(1, d / lengths[i]) : 1);
    d -= lengths[i];
  }
  return out.copy(points[points.length - 1]);
}

const ease = (x: number) => x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
const easeOut = (x: number) => 1 - Math.pow(1 - x, 2.2);
const easeIn = (x: number) => x * x;

export type Pose = {position: THREE.Vector3; heading: number | null; visible: boolean; airborne: boolean; state: FlightState};

export class Flightpath {
  private layout: Layout;
  private pad = new Map<string, number>();
  constructor(layout: Layout, schedules: Schedule[]) {
    this.layout = layout;
    schedules.filter(s => s.surfaceWait > 0).sort((a, b) => a.arrival - b.arrival).forEach((s, i) => this.pad.set(s.flightId, i % layout.pads.length));
  }
  private runway(s: Schedule) { return this.layout.runways.find(r => r.id === s.runway) ?? this.layout.runways[0]; }
  private stand(s: Schedule) { return this.layout.stands.find(x => x.id === s.stand) ?? this.layout.stands[0]; }
  private altitude(id: string) { return 9 + (this.layout.slot.get(id) ?? 0) * 2.4; }
  private entryAngle(id: string) { const d = this.layout.inbound.get(id) ?? V(-1, 0, 0); return Math.atan2(d.z, d.x); }
  holdPoint(id: string, minutes: number, out = new THREE.Vector3()) {
    const a = this.entryAngle(id) + minutes * HOLD_RATE;
    return out.set(HOLD_CENTER.x + Math.cos(a) * HOLD_RADIUS, this.altitude(id), HOLD_CENTER.z + Math.sin(a) * HOLD_RADIUS);
  }

  position(s: Schedule, t: number, out: THREE.Vector3): {heading: number | null; airborne: boolean; visible: boolean; state: FlightState} {
    const st = stateAt(t, s);
    const rw = this.runway(s), stand = this.stand(s);
    const fix = Math.min(s.reference, s.arrival) - APPROACH;
    const exit = V(rw.exit, 0, rw.z), taxiJoin = V(rw.exit + 2, 0, TAXI_Z);
    switch (st.phase) {
      case 'enroute': {
        const dir = this.layout.inbound.get(s.flightId) ?? V(-1, 0, 0);
        this.holdPoint(s.flightId, 0, out).add(dir.clone().multiplyScalar((1 - st.progress) * 90));
        out.y = this.altitude(s.flightId) + (1 - st.progress) * 10;
        return {heading: null, airborne: true, visible: true, state: st};
      }
      case 'holding':
        this.holdPoint(s.flightId, t - fix, out);
        return {heading: null, airborne: true, visible: true, state: st};
      case 'approach': {
        const start = this.holdPoint(s.flightId, Math.max(0, s.arrival - APPROACH - fix));
        along([start, V(-72, 6.5, rw.z), V(rw.touchdown, 0.35, rw.z)], ease(st.progress), out);
        return {heading: null, airborne: true, visible: true, state: st};
      }
      case 'landing':
        out.set(THREE.MathUtils.lerp(rw.touchdown, rw.exit, easeOut(st.progress)), 0.3, rw.z);
        return {heading: 0, airborne: false, visible: true, state: st};
      case 'taxi_in': {
        const target = s.surfaceWait > 0 ? this.layout.pads[this.pad.get(s.flightId) ?? 0] : stand.pos;
        const leadIn = s.surfaceWait > 0 ? V(target.x, 0, TAXI_Z) : stand.leadIn;
        along([exit, taxiJoin, V(leadIn.x, 0, TAXI_Z), leadIn, target], st.progress, out);
        out.y = 0.3;
        return {heading: null, airborne: false, visible: true, state: st};
      }
      case 'surface_hold':
        out.copy(this.layout.pads[this.pad.get(s.flightId) ?? 0]); out.y = 0.3;
        return {heading: Math.PI / 2, airborne: false, visible: true, state: st};
      case 'to_stand': {
        const pad = this.layout.pads[this.pad.get(s.flightId) ?? 0];
        along([pad, V(pad.x, 0, TAXI_Z), V(stand.leadIn.x, 0, TAXI_Z), stand.leadIn, stand.pos], st.progress, out);
        out.y = 0.3;
        return {heading: null, airborne: false, visible: true, state: st};
      }
      case 'turnaround':
        out.copy(stand.pos); out.y = 0.3;
        return {heading: Math.PI / 2, airborne: false, visible: true, state: st};
      case 'taxi_out': {
        const moving = Math.min(1, (s.departure - s.offBlock) > 0 ? 12 / (s.departure - s.offBlock) : 1);
        const p = st.progress;
        if (p < 0.12) { along([stand.pos, stand.leadIn], p / 0.12, out); out.y = 0.3; return {heading: Math.PI / 2, airborne: false, visible: true, state: st}; }
        const f = Math.min(1, (p - 0.12) / Math.max(0.05, 0.88 * moving));
        along([stand.leadIn, V(stand.leadIn.x, 0, TAXI_Z), V(rw.entry, 0, TAXI_Z), V(rw.entry, 0, rw.z - 3.5)], f, out);
        if (p > 0.97) out.set(rw.entry, 0, THREE.MathUtils.lerp(rw.z - 3.5, rw.z, (p - 0.97) / 0.03));
        out.y = 0.3;
        return {heading: null, airborne: false, visible: true, state: st};
      }
      case 'takeoff': {
        const p = st.progress;
        if (p < 0.55) out.set(THREE.MathUtils.lerp(rw.entry, 30, easeIn(p / 0.55)), 0.3, rw.z);
        else { const q = (p - 0.55) / 0.45; out.set(THREE.MathUtils.lerp(30, 140, q), 0.3 + 22 * q * q, rw.z); }
        return {heading: 0, airborne: p > 0.55, visible: true, state: st};
      }
      default:
        out.set(200, 30, rw.z);
        return {heading: 0, airborne: true, visible: false, state: st};
    }
  }
}
