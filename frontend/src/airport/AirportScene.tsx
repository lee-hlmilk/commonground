import { useEffect, useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Html, Line, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import type { Plan, Scenario } from '../types';
import { simClock } from '../lib/clock';
import { clock as fmt, FLIGHT_COLORS } from '../lib/format';
import { PHASE_LABEL, runwayClosed, schedules as buildSchedules, standStatus, type Schedule } from '../lib/sim';
import { buildLayout, Flightpath, HOLD_CENTER, HOLD_RADIUS, TAXI_Z, type Layout } from './layout';
import { ViewOffset, type Insets } from '../lib/ViewOffset';
import { Effects } from '../lib/Effects';

export type Beam = {id: string; from: string; to: string[]; color: string};
export type CameraPreset = 'overview' | 'airspace' | 'apron' | 'runway';

type Props = {
  scenario: Scenario;
  plan: Plan | null;
  beams: Beam[];
  onBeamDone: (id: string) => void;
  selectedFlight: string | null;
  onSelectFlight: (id: string | null) => void;
  selectedStand: string | null;
  onSelectStand: (id: string) => void;
  preset: CameraPreset;
  presetNonce: number;
  insets: Insets;
  active: boolean;
};

const PRESETS: Record<CameraPreset, {pos: [number, number, number]; target: [number, number, number]}> = {
  overview: {pos: [-16, 56, 74], target: [-16, 0, -14]},
  airspace: {pos: [-44, 26, 26], target: [-80, 10, -30]},
  apron: {pos: [12, 48, 44], target: [12, 0, -17]},
  runway: {pos: [-72, 7, 16], target: [-10, 0, -2]},
};

export default function AirportScene(props: Props) {
  return <Canvas className="scene-canvas" frameloop={props.active ? 'always' : 'never'} shadows dpr={[1, 2]} camera={{position: PRESETS.overview.pos, fov: 45, near: 0.5, far: 900}}
    onPointerMissed={() => props.onSelectFlight(null)}>
    <ViewOffset {...props.insets}/>
    <color attach="background" args={['#060b16']}/>
    <fog attach="fog" args={['#0a1426', 150, 460]}/>
    <Sky/>
    <hemisphereLight args={['#8fb4ee', '#0c1220', 1.15]}/>
    <ambientLight intensity={0.5}/>
    <directionalLight position={[40, 90, 60]} intensity={1.8} castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004}
      shadow-camera-left={-80} shadow-camera-right={80} shadow-camera-top={60} shadow-camera-bottom={-60}/>
    <World {...props}/>
    <Effects intensity={0.75} threshold={0.7}/>
  </Canvas>;
}

function Sky() {
  const material = useMemo(() => new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: 'varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: 'varying vec3 vP; void main(){ float h = clamp(vP.y, 0.0, 1.0); vec3 horizon = vec3(0.07,0.13,0.25); vec3 zenith = vec3(0.012,0.02,0.045); vec3 c = mix(horizon, zenith, pow(h, 0.45)); c += vec3(0.10,0.06,0.02) * pow(1.0 - h, 10.0); gl_FragColor = vec4(c,1.0); }',
  }), []);
  return <mesh material={material} renderOrder={-1}><sphereGeometry args={[600, 32, 16]}/></mesh>;
}

function World({scenario, plan, beams, onBeamDone, selectedFlight, onSelectFlight, selectedStand, onSelectStand, preset, presetNonce}: Omit<Props, 'insets' | 'active'>) {
  const layout = useMemo(() => buildLayout(scenario), [scenario]);
  const scheds = useMemo(() => buildSchedules(scenario, plan), [scenario, plan]);
  const path = useMemo(() => new Flightpath(layout, scheds), [layout, scheds]);
  const positions = useMemo(() => new Map<string, THREE.Vector3>(), []);
  const colorOf = (id: string) => FLIGHT_COLORS[scenario.flights.findIndex(f => f.id === id) % FLIGHT_COLORS.length];
  return <>
    <Ground/>
    {layout.runways.map(r => <Runway key={r.id} runway={r} scenario={scenario} scheds={scheds} colorOf={colorOf}/>)}
    <Taxiways layout={layout}/>
    <Terminal layout={layout}/>
    <Floodlights layout={layout}/>
    <Tower position={layout.tower}/>
    <Depot position={layout.depot} teams={scenario.resources.handling_teams}/>
    {layout.pads.map((p, i) => <HoldingPad key={i} position={p} index={i}/>)}
    {layout.stands.map(s => <Stand key={s.id} stand={s} scenario={scenario} scheds={scheds} colorOf={colorOf}
      selected={selectedStand === s.id} onSelect={() => onSelectStand(s.id)}/>)}
    <HoldingStack scheds={scheds} path={path} colorOf={colorOf}/>
    <ApproachGuide layout={layout}/>
    <Vehicles layout={layout} scheds={scheds} teams={scenario.resources.handling_teams}/>
    {scenario.flights.map((f, i) => <Aircraft key={f.id} id={f.id} callsign={f.callsign} color={FLIGHT_COLORS[i % FLIGHT_COLORS.length]}
      schedule={scheds[i]} path={path} positions={positions} selected={selectedFlight === f.id} clockStart={scenario.clock_start}
      onSelect={() => onSelectFlight(f.id)}/>)}
    {beams.map(b => <BeamLine key={b.id} beam={b} layout={layout} positions={positions} onDone={onBeamDone}/>)}
    <CameraRig preset={preset} nonce={presetNonce} follow={selectedFlight} positions={positions}/>
  </>;
}

function Ground() {
  return <group>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.05, 0]} receiveShadow>
      <planeGeometry args={[1400, 1400]}/>
      <meshStandardMaterial color="#0b1526" roughness={1}/>
    </mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[6, -0.03, -10]} receiveShadow>
      <planeGeometry args={[124, 48]}/>
      <meshStandardMaterial color="#111d31" roughness={0.95}/>
    </mesh>
    <gridHelper args={[600, 120, '#12294a', '#0d1b31']} position={[0, -0.02, 0]}/>
  </group>;
}

function Runway({runway: r, scenario, scheds, colorOf}: {runway: Layout['runways'][number]; scenario: Scenario; scheds: Schedule[]; colorOf: (id: string) => string}) {
  const glow = useRef<THREE.MeshBasicMaterial>(null);
  const closed = useRef<THREE.Mesh>(null);
  const label = useRef<HTMLDivElement>(null);
  const length = r.x1 - r.x0;
  const closures = scenario.resources.runways.find(x => x.id === r.id)?.closures ?? [];
  const dashes = useMemo(() => Array.from({length: 22}, (_, i) => r.x0 + 4 + i * (length - 8) / 21), [r, length]);
  const lights = useMemo(() => Array.from({length: 30}, (_, i) => r.x0 + i * length / 29), [r, length]);
  useFrame(({clock}) => {
    const t = simClock.t;
    const isClosed = runwayClosed(scenario, r.id, t);
    if (closed.current) {
      closed.current.visible = isClosed;
      (closed.current.material as THREE.MeshBasicMaterial).opacity = 0.32 + Math.sin(clock.elapsedTime * 4) * 0.1;
    }
    const mover = scheds.find(s => s.runway === r.id && ((t >= s.arrival - 0.6 && t < s.arrival + 1) || (t >= s.departure && t < s.departure + 1.3)));
    if (glow.current) {
      glow.current.color.set(isClosed ? '#ff4d6d' : mover ? colorOf(mover.flightId) : '#2b5c9a');
      glow.current.opacity = isClosed ? 0.9 : mover ? 1 : 0.55;
    }
    if (label.current) {
      const active = closures.find(c => t >= c.start && t < c.end);
      const next = closures.find(c => c.start > t);
      const text = active ? `CLOSED until ${fmt(scenario.clock_start, active.end)}` : next ? `Open · closure ${fmt(scenario.clock_start, next.start)}–${fmt(scenario.clock_start, next.end)}` : mover ? 'Movement in progress' : 'Open';
      if (label.current.dataset.text !== text) { label.current.dataset.text = text; label.current.lastElementChild!.textContent = text; label.current.classList.toggle('closed', !!active); }
    }
  });
  return <group position={[0, 0, r.z]}>
    <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[(r.x0 + r.x1) / 2, 0.01, 0]}>
      <planeGeometry args={[length, 3.6]}/>
      <meshStandardMaterial color="#141b28" roughness={0.85}/>
    </mesh>
    {dashes.map(x => <mesh key={x} rotation={[-Math.PI / 2, 0, 0]} position={[x, 0.03, 0]}><planeGeometry args={[1.6, 0.14]}/><meshBasicMaterial color="#d9e3f2"/></mesh>)}
    {[r.x0 + 1.2, r.x1 - 1.2].map(x => <group key={x}>{[-1.2, -0.7, -0.2, 0.3, 0.8, 1.3].map(z => <mesh key={z} rotation={[-Math.PI / 2, 0, 0]} position={[x, 0.03, z - 0.05]}><planeGeometry args={[1.8, 0.24]}/><meshBasicMaterial color="#d9e3f2"/></mesh>)}</group>)}
    {lights.flatMap(x => [-1.95, 1.95].map(z => <mesh key={`${x}${z}`} position={[x, 0.08, z]}><sphereGeometry args={[0.09, 8, 8]}/><meshBasicMaterial color="#7fb2ff" toneMapped={false}/></mesh>))}
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[(r.x0 + r.x1) / 2, 0.02, 0]}>
      <planeGeometry args={[length, 4.4]}/>
      <meshBasicMaterial ref={glow} color="#2b5c9a" transparent opacity={0.55} blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false}
        alphaMap={edgeTexture()}/>
    </mesh>
    <mesh ref={closed} position={[(r.x0 + r.x1) / 2, 0.6, 0]} visible={false}>
      <boxGeometry args={[length, 1.2, 4]}/>
      <meshBasicMaterial color="#ff2e55" transparent opacity={0.35} depthWrite={false} toneMapped={false}/>
    </mesh>
    <Html zIndexRange={[9, 0]} position={[r.x0 - 4, 0.5, 3.4]} center style={{pointerEvents: 'none'}}>
      <div ref={label} className="scene-tag runway"><b>{r.id}</b><span/></div>
    </Html>
  </group>;
}

let edgeTex: THREE.Texture | null = null;
function edgeTexture() {
  if (edgeTex) return edgeTex;
  const c = document.createElement('canvas'); c.width = 4; c.height = 64;
  const g = c.getContext('2d')!;
  const grad = g.createLinearGradient(0, 0, 0, 64);
  grad.addColorStop(0, '#fff'); grad.addColorStop(0.12, '#000'); grad.addColorStop(0.88, '#000'); grad.addColorStop(1, '#fff');
  g.fillStyle = grad; g.fillRect(0, 0, 4, 64);
  edgeTex = new THREE.CanvasTexture(c);
  return edgeTex;
}

function Taxiways({layout}: {layout: Layout}) {
  const r = layout.runways[0];
  const xs = [r.entry, r.exit + 2, ...layout.stands.map(s => s.leadIn.x), ...layout.pads.map(p => p.x)];
  const minX = Math.min(...xs) - 2, maxX = Math.max(...xs) + 2;
  const centre: [number, number, number][] = [[minX, 0.04, TAXI_Z], [maxX, 0.04, TAXI_Z]];
  return <group>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[(minX + maxX) / 2, 0.015, TAXI_Z]} receiveShadow><planeGeometry args={[maxX - minX, 2]}/><meshStandardMaterial color="#111a29"/></mesh>
    <Line points={centre} color="#f5c542" lineWidth={1.2} transparent opacity={0.8}/>
    {[r.entry, r.exit + 2].map(x => <group key={x}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[x, 0.015, (r.z + TAXI_Z) / 2]}><planeGeometry args={[2, Math.abs(TAXI_Z - r.z)]}/><meshStandardMaterial color="#111a29"/></mesh>
      <Line points={[[x, 0.04, r.z - 1.9], [x, 0.04, TAXI_Z]]} color="#f5c542" lineWidth={1.2} transparent opacity={0.8}/>
    </group>)}
    <Line points={[[r.entry - 1.2, 0.05, r.z - 3.5], [r.entry + 1.2, 0.05, r.z - 3.5]]} color="#ff9f43" lineWidth={3}/>
    {layout.stands.map(s => <Line key={s.id} points={[[s.leadIn.x, 0.04, TAXI_Z], [s.pos.x, 0.04, s.pos.z]]} color="#f5c542" lineWidth={1} transparent opacity={0.55} dashed dashSize={0.6} gapSize={0.4}/>)}
    {layout.pads.map((p, i) => <Line key={`pad-${i}`} points={[[p.x, 0.04, TAXI_Z], [p.x, 0.04, p.z]]} color="#a78bfa" lineWidth={1} transparent opacity={0.65} dashed dashSize={0.6} gapSize={0.4}/>)}
  </group>;
}

function Terminal({layout}: {layout: Layout}) {
  const {x0, x1, z} = layout.terminal;
  const w = x1 - x0;
  return <group position={[(x0 + x1) / 2, 0, z]}>
    <mesh position={[0, 2, 0]} castShadow receiveShadow><boxGeometry args={[w, 4, 5]}/><meshStandardMaterial color="#13203a" metalness={0.3} roughness={0.4}/></mesh>
    <mesh position={[0, 2.3, 2.52]}><planeGeometry args={[w - 1, 1.2]}/><meshBasicMaterial color="#5aa9ff" transparent opacity={0.45} toneMapped={false}/></mesh>
    <mesh position={[0, 4.15, 0]}><boxGeometry args={[w + 0.6, 0.3, 5.6]}/><meshStandardMaterial color="#1d2d4d"/></mesh>
  </group>;
}

function Floodlights({layout}: {layout: Layout}) {
  const xs = layout.stands.map(s => s.pos.x);
  const poles = [Math.min(...xs) - 6, (Math.min(...xs) + Math.max(...xs)) / 2, Math.max(...xs) + 6];
  return <group>{poles.map(x => <group key={x} position={[x, 0, -24]}>
    <mesh position={[0, 4, 0]}><cylinderGeometry args={[0.12, 0.16, 8, 8]}/><meshStandardMaterial color="#2a3550"/></mesh>
    <mesh position={[0, 8.1, 0]}><boxGeometry args={[1.4, 0.3, 0.5]}/><meshBasicMaterial color="#ffe6b0" toneMapped={false}/></mesh>
    <pointLight position={[0, 7.6, 2]} color="#ffd9a0" intensity={60} distance={30} decay={1.6}/>
  </group>)}</group>;
}

function Tower({position}: {position: THREE.Vector3}) {
  const beacon = useRef<THREE.MeshBasicMaterial>(null);
  useFrame(({clock}) => { if (beacon.current) beacon.current.opacity = 0.4 + 0.6 * Math.max(0, Math.sin(clock.elapsedTime * 3)); });
  return <group position={position}>
    <mesh position={[0, 5, 0]} castShadow><cylinderGeometry args={[0.7, 1.1, 10, 16]}/><meshStandardMaterial color="#1a2a48" metalness={0.4} roughness={0.5}/></mesh>
    <mesh position={[0, 10.8, 0]} castShadow><cylinderGeometry args={[2, 1.4, 1.8, 8]}/><meshStandardMaterial color="#5aa9ff" emissive="#1d5bb0" emissiveIntensity={0.8} transparent opacity={0.85}/></mesh>
    <mesh position={[0, 12, 0]}><cylinderGeometry args={[2.2, 2.2, 0.3, 8]}/><meshStandardMaterial color="#20345a"/></mesh>
    <mesh position={[0, 12.7, 0]}><sphereGeometry args={[0.25, 12, 12]}/><meshBasicMaterial ref={beacon} color="#ff4d6d" transparent toneMapped={false}/></mesh>
    <Html zIndexRange={[9, 0]} position={[0, 14.5, 0]} center style={{pointerEvents: 'none'}}><div className="scene-tag atc"><i/>ATC</div></Html>
  </group>;
}

function Depot({position, teams}: {position: THREE.Vector3; teams: number}) {
  return <group position={position}>
    <mesh position={[0, 1.4, 0]} castShadow receiveShadow><boxGeometry args={[7, 2.8, 4.5]}/><meshStandardMaterial color="#2a2112" metalness={0.2} roughness={0.6}/></mesh>
    <mesh position={[0, 1.2, 2.27]}><planeGeometry args={[5, 1.8]}/><meshBasicMaterial color="#ffb547" transparent opacity={0.35} toneMapped={false}/></mesh>
    <Html zIndexRange={[9, 0]} position={[0, 4.2, 0]} center style={{pointerEvents: 'none'}}><div className="scene-tag ground"><i/>Ground ops · {teams} team{teams === 1 ? '' : 's'}</div></Html>
  </group>;
}

function HoldingPad({position, index}: {position: THREE.Vector3; index: number}) {
  return <group position={position}>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}><ringGeometry args={[2.9, 3.15, 4, 1, Math.PI / 4]}/><meshBasicMaterial color="#a78bfa" transparent opacity={0.7} toneMapped={false}/></mesh>
    {index === 0 && <Html zIndexRange={[9, 0]} position={[-4.2, 0.4, 0]} center style={{pointerEvents: 'none'}}><div className="scene-tag hold surf">Surface hold</div></Html>}
  </group>;
}

function Stand({stand, scenario, scheds, colorOf, selected, onSelect}: {stand: Layout['stands'][number]; scenario: Scenario; scheds: Schedule[]; colorOf: (id: string) => string; selected: boolean; onSelect: () => void}) {
  const pad = useRef<THREE.MeshBasicMaterial>(null);
  const ring = useRef<THREE.MeshBasicMaterial>(null);
  const label = useRef<HTMLDivElement>(null);
  const callsign = (id: string) => scenario.flights.find(f => f.id === id)?.callsign ?? id;
  useFrame(({clock}) => {
    const st = standStatus(scenario, scheds, stand.id, simClock.t);
    const color = st.state === 'occupied' ? colorOf(st.flightId) : st.state === 'clearing' ? '#ffb547' : st.state === 'unavailable' ? '#ff4d6d' : st.state === 'reserved' ? '#8aa4c8' : '#38e1d0';
    if (pad.current) { pad.current.color.set(color); pad.current.opacity = st.state === 'unavailable' ? 0.28 + 0.12 * Math.sin(clock.elapsedTime * 3) : 0.2; }
    if (ring.current) ring.current.color.set(color);
    if (label.current) {
      const text = st.state === 'occupied' ? callsign(st.flightId) : st.state === 'clearing' ? 'Pushback clearing' : st.state === 'unavailable' ? `Closed → ${fmt(scenario.clock_start, st.until)}` : st.state === 'reserved' ? `Next ${fmt(scenario.clock_start, st.at)}` : 'Free';
      if (label.current.dataset.text !== text) { label.current.dataset.text = text; label.current.lastElementChild!.textContent = text; label.current.dataset.state = st.state; }
    }
  });
  return <group position={stand.pos} onClick={e => { e.stopPropagation(); onSelect(); }}>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}><planeGeometry args={[9, 9]}/><meshBasicMaterial ref={pad} color="#38e1d0" transparent opacity={0.2} depthWrite={false} toneMapped={false}/></mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, 0]}><ringGeometry args={[4.35, 4.55, 4, 1, Math.PI / 4]}/><meshBasicMaterial ref={ring} color="#38e1d0" toneMapped={false}/></mesh>
    {selected && <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.04, 0]}><ringGeometry args={[4.9, 5.2, 4, 1, Math.PI / 4]}/><meshBasicMaterial color="#ffffff" toneMapped={false}/></mesh>}
    <Html zIndexRange={[9, 0]} position={[0, 0.5, 6.2]} center style={{pointerEvents: 'none'}}><div ref={label} className="scene-tag stand"><b>{stand.id}</b><span/></div></Html>
  </group>;
}

function HoldingStack({scheds, path, colorOf}: {scheds: Schedule[]; path: Flightpath; colorOf: (id: string) => string}) {
  const rings = useMemo(() => scheds.filter(s => !s.planned || s.airborneWait > 0).map(s => {
    const pts = Array.from({length: 65}, (_, i) => path.holdPoint(s.flightId, 0).clone().setX(HOLD_CENTER.x + Math.cos(i / 64 * Math.PI * 2) * HOLD_RADIUS).setZ(HOLD_CENTER.z + Math.sin(i / 64 * Math.PI * 2) * HOLD_RADIUS));
    return {id: s.flightId, pts};
  }), [scheds, path]);
  return <group>
    {rings.map(r => <Line key={r.id} points={r.pts} color={colorOf(r.id)} lineWidth={1} transparent opacity={0.45} dashed dashSize={1.2} gapSize={0.8}/>)}
    <mesh position={[HOLD_CENTER.x, 0.05, HOLD_CENTER.z]} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[HOLD_RADIUS - 0.2, HOLD_RADIUS + 0.2, 64]}/><meshBasicMaterial color="#ffb547" transparent opacity={0.25} toneMapped={false}/></mesh>
    <Html zIndexRange={[9, 0]} position={[HOLD_CENTER.x, 2, HOLD_CENTER.z + HOLD_RADIUS + 3]} center style={{pointerEvents: 'none'}}><div className="scene-tag hold">Holding stack</div></Html>
  </group>;
}

function ApproachGuide({layout}: {layout: Layout}) {
  const r = layout.runways[0];
  return <Line points={[[-82, 6.5, r.z], [r.touchdown, 0.35, r.z]]} color="#5aa9ff" lineWidth={1} transparent opacity={0.35} dashed dashSize={1.5} gapSize={1}/>;
}

function Vehicles({layout, scheds, teams}: {layout: Layout; scheds: Schedule[]; teams: number}) {
  const refs = useRef<(THREE.Group | null)[]>([]);
  useFrame((_, delta) => {
    const t = simClock.t;
    const busy = scheds.filter(s => t >= s.inBlock && t < s.offBlock).sort((a, b) => a.inBlock - b.inBlock);
    refs.current.forEach((g, i) => {
      if (!g) return;
      const job = busy[i];
      const stand = job && layout.stands.find(s => s.id === job.stand);
      const home = new THREE.Vector3(layout.depot.x - 2.5 + i * 2.2, 0.35, layout.depot.z + 4);
      const target = stand ? new THREE.Vector3(stand.pos.x + 4.4, 0.35, stand.pos.z + 2) : home;
      const prev = g.position.clone();
      g.position.lerp(target, Math.min(1, delta * 2.5));
      const d = g.position.clone().sub(prev);
      if (d.lengthSq() > 1e-6) g.rotation.y = Math.atan2(-d.z, d.x);
    });
  });
  return <group>{Array.from({length: teams}, (_, i) => <group key={i} ref={el => { refs.current[i] = el; }} position={[layout.depot.x - 2.5 + i * 2.2, 0.35, layout.depot.z + 4]}>
    <mesh castShadow><boxGeometry args={[1.3, 0.55, 0.75]}/><meshStandardMaterial color="#ffb547" emissive="#7a4a00" emissiveIntensity={0.6}/></mesh>
    <mesh position={[0.35, 0.42, 0]}><boxGeometry args={[0.5, 0.35, 0.65]}/><meshStandardMaterial color="#2a2112"/></mesh>
    <mesh position={[0, 0.7, 0]}><sphereGeometry args={[0.09, 8, 8]}/><meshBasicMaterial color="#ffcf6b" toneMapped={false}/></mesh>
  </group>)}</group>;
}

function Aircraft({id, callsign, color, schedule, path, positions, selected, clockStart, onSelect}: {id: string; callsign: string; color: string; schedule: Schedule; path: Flightpath; positions: Map<string, THREE.Vector3>; selected: boolean; clockStart: string; onSelect: () => void}) {
  const group = useRef<THREE.Group>(null);
  const halo = useRef<THREE.Mesh>(null);
  const label = useRef<HTMLDivElement>(null);
  const pos = useMemo(() => new THREE.Vector3(), []);
  const ahead = useMemo(() => new THREE.Vector3(), []);
  useEffect(() => () => { positions.delete(id); }, [id, positions]);
  useFrame(({clock}) => {
    const g = group.current;
    if (!g) return;
    const t = simClock.t;
    const res = path.position(schedule, t, pos);
    g.visible = res.visible;
    g.position.copy(pos);
    positions.set(id, pos.clone());
    if (res.heading !== null) g.rotation.set(0, res.heading, 0);
    else {
      path.position(schedule, t + 0.05, ahead);
      const d = ahead.sub(pos);
      if (d.lengthSq() > 1e-8) {
        const yaw = Math.atan2(-d.z, d.x);
        const pitch = Math.atan2(d.y, Math.hypot(d.x, d.z));
        const bank = res.state.phase === 'holding' ? 0.45 : 0;
        g.rotation.set(bank, yaw, pitch, 'YZX');
      }
    }
    if (halo.current) {
      halo.current.visible = !res.airborne;
      halo.current.scale.setScalar(1 + (selected ? 0.25 * Math.sin(clock.elapsedTime * 4) : 0));
    }
    if (label.current) {
      const st = res.state;
      const extra = st.phase === 'holding' ? (schedule.planned ? `+${schedule.airborneWait} min hold · lands ${fmt(clockStart, schedule.arrival)}` : 'awaiting a coordinated plan')
        : st.phase === 'enroute' ? `ref. arrival ${fmt(clockStart, schedule.reference)}`
        : st.phase === 'surface_hold' ? `stand ${schedule.stand} at ${fmt(clockStart, schedule.inBlock)}`
        : st.phase === 'turnaround' ? `${schedule.stand} · ${Math.round(st.progress * 100)}%`
        : st.phase === 'taxi_out' ? `wheels-up ${fmt(clockStart, schedule.departure)}`
        : schedule.stand ? `→ ${schedule.stand}` : '';
      label.current.style.opacity = st.phase === 'enroute' && !selected ? String(Math.max(0, Math.min(1, (st.progress - 0.4) * 3))) : '1';
      const text = `${PHASE_LABEL[st.phase]}${extra ? ' · ' + extra : ''}`;
      if (label.current.dataset.text !== text) { label.current.dataset.text = text; label.current.lastElementChild!.textContent = text; }
    }
  });
  return <group ref={group} onClick={e => { e.stopPropagation(); onSelect(); }}
    onPointerOver={() => { document.body.style.cursor = 'pointer'; }} onPointerOut={() => { document.body.style.cursor = ''; }}>
    <group scale={1.9}>
      <mesh castShadow rotation={[0, 0, Math.PI / 2]}><capsuleGeometry args={[0.28, 2.6, 6, 14]}/><meshStandardMaterial color="#e9eef6" metalness={0.35} roughness={0.35}/></mesh>
      <mesh castShadow position={[0.1, -0.05, 0]}><boxGeometry args={[0.75, 0.06, 3.3]}/><meshStandardMaterial color="#cfd8e6" metalness={0.3} roughness={0.4}/></mesh>
      <mesh castShadow position={[-1.35, 0.05, 0]}><boxGeometry args={[0.4, 0.05, 1.3]}/><meshStandardMaterial color="#cfd8e6"/></mesh>
      <mesh castShadow position={[-1.35, 0.42, 0]}><boxGeometry args={[0.55, 0.75, 0.06]}/><meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.55}/></mesh>
      {[-0.85, 0.85].map(z => <mesh key={z} position={[0.25, -0.22, z]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.13, 0.13, 0.5, 10]}/><meshStandardMaterial color="#8b96a8" metalness={0.6}/></mesh>)}
      <mesh position={[0.15, -0.03, 1.66]}><sphereGeometry args={[0.06, 6, 6]}/><meshBasicMaterial color="#4ade80" toneMapped={false}/></mesh>
      <mesh position={[0.15, -0.03, -1.66]}><sphereGeometry args={[0.06, 6, 6]}/><meshBasicMaterial color="#ff4d6d" toneMapped={false}/></mesh>
    </group>
    <mesh ref={halo} rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.25, 0]}><ringGeometry args={[3.4, 3.75, 40]}/><meshBasicMaterial color={color} transparent opacity={selected ? 0.95 : 0.55} toneMapped={false}/></mesh>
    <Html zIndexRange={[9, 0]} position={[0, 2.6, 0]} center style={{pointerEvents: 'none'}} >
      <div ref={label} className={`aircraft-tag ${selected ? 'selected' : ''}`} style={{'--c': color} as React.CSSProperties}><b>{callsign}</b><span/></div>
    </Html>
  </group>;
}

function BeamLine({beam, layout, positions, onDone}: {beam: Beam; layout: Layout; positions: Map<string, THREE.Vector3>; onDone: (id: string) => void}) {
  const start = useRef<number | null>(null);
  const finished = useRef(false);
  const where = (pid: string) => pid === 'atc' ? layout.tower.clone().setY(12) : pid === 'ground' ? layout.depot.clone().setY(3.2) : positions.get(pid.replace(/^pilot-/, ''))?.clone().add(new THREE.Vector3(0, 0.6, 0)) ?? null;
  const lines = useMemo(() => beam.to.map(() => {
    const geometry = new THREE.BufferGeometry().setFromPoints(Array.from({length: 32}, () => new THREE.Vector3()));
    const material = new THREE.LineBasicMaterial({color: beam.color, transparent: true, opacity: 0.9, toneMapped: false, depthWrite: false});
    return new THREE.Line(geometry, material);
  }), [beam]);
  const pulses = useMemo(() => beam.to.map(() => new THREE.Mesh(new THREE.SphereGeometry(0.45, 12, 12), new THREE.MeshBasicMaterial({color: beam.color, toneMapped: false, transparent: true}))), [beam]);
  useEffect(() => () => { lines.forEach(l => { l.geometry.dispose(); (l.material as THREE.Material).dispose(); }); pulses.forEach(p => { p.geometry.dispose(); (p.material as THREE.Material).dispose(); }); }, [lines, pulses]);
  const curve = useMemo(() => new THREE.QuadraticBezierCurve3(), []);
  useFrame(({clock}) => {
    if (start.current === null) start.current = clock.elapsedTime;
    const age = (clock.elapsedTime - start.current) / 2.2;
    if (age >= 1) { if (!finished.current) { finished.current = true; onDone(beam.id); } return; }
    const a = where(beam.from);
    beam.to.forEach((rid, i) => {
      const b = where(rid);
      const line = lines[i], pulse = pulses[i];
      if (!a || !b) { line.visible = pulse.visible = false; return; }
      const mid = a.clone().lerp(b, 0.5); mid.y += 6 + a.distanceTo(b) * 0.15;
      curve.v0.copy(a); curve.v1.copy(mid); curve.v2.copy(b);
      const pts = curve.getPoints(31);
      line.geometry.setFromPoints(pts);
      (line.material as THREE.LineBasicMaterial).opacity = 0.85 * (1 - age);
      pulse.position.copy(curve.getPoint(Math.min(1, age * 1.6)));
      (pulse.material as THREE.MeshBasicMaterial).opacity = 1 - age;
    });
  });
  return <group>{lines.map((l, i) => <primitive key={`l${i}`} object={l}/>)}{pulses.map((p, i) => <primitive key={`p${i}`} object={p}/>)}</group>;
}

function CameraRig({preset, nonce, follow, positions}: {preset: CameraPreset; nonce: number; follow: string | null; positions: Map<string, THREE.Vector3>}) {
  const controls = useRef<OrbitControlsImpl>(null);
  const {camera} = useThree();
  const goal = useRef<{pos: THREE.Vector3; target: THREE.Vector3} | null>(null);
  const lastFollow = useRef<THREE.Vector3 | null>(null);
  useEffect(() => {
    const p = PRESETS[preset];
    goal.current = {pos: new THREE.Vector3(...p.pos), target: new THREE.Vector3(...p.target)};
  }, [preset, nonce]);
  useEffect(() => { lastFollow.current = null; if (follow) goal.current = null; }, [follow]);
  useFrame((_, delta) => {
    const c = controls.current;
    if (!c) return;
    const k = Math.min(1, delta * 2.2);
    if (follow) {
      const p = positions.get(follow);
      if (p) {
        if (!lastFollow.current) {
          const offset = camera.position.clone().sub(c.target).setLength(26);
          c.target.lerp(p, k);
          camera.position.lerp(p.clone().add(offset), k);
          if (c.target.distanceTo(p) < 0.5) lastFollow.current = p.clone();
        } else {
          const d = p.clone().sub(lastFollow.current);
          c.target.add(d); camera.position.add(d);
          lastFollow.current.copy(p);
        }
      }
    } else if (goal.current) {
      camera.position.lerp(goal.current.pos, k);
      c.target.lerp(goal.current.target, k);
      if (camera.position.distanceTo(goal.current.pos) < 0.3) goal.current = null;
    }
    c.update();
  });
  return <OrbitControls ref={controls} makeDefault enableDamping dampingFactor={0.08} maxPolarAngle={Math.PI / 2.08} minDistance={8} maxDistance={320}
    target={PRESETS.overview.target} onStart={() => { goal.current = null; }}/>;
}
