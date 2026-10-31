import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Html, Line, OrbitControls, Stars } from '@react-three/drei';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import type { Network, NetworkAirport } from '../types';
import { ViewOffset, type Insets } from '../lib/ViewOffset';
import { Effects } from '../lib/Effects';
import { R, arcPoints, landDots, roundDot, sampleArc, stateBorders, toVec3, weatherColor } from './geo';

export type GlobeHighlight = {origin: string; destination: string; color: string; label: string};

type Props = {
  network: Network | null;
  selected: string | null;
  onSelect: (code: string | null) => void;
  highlights: GlobeHighlight[];
  routeLimit: number;
  weatherOnly: boolean;
  insets: Insets;
  active: boolean;
};

const BASE_ROUTES = 1400;
const MOVERS = 420;

export default function GlobeScene(props: Props) {
  return <Canvas className="scene-canvas" frameloop={props.active ? 'always' : 'never'} dpr={[1, 2]} camera={{position: toVec3(34, -96, 2.75).toArray(), fov: 42, near: 0.01, far: 100}} gl={{antialias: true, alpha: false}}>
    <ViewOffset {...props.insets}/>
    <color attach="background" args={['#03060d']}/>
    <fog attach="fog" args={['#03060d', 4, 9]}/>
    <Stars radius={30} depth={20} count={2600} factor={2.4} saturation={0} fade speed={0.4}/>
    <Earth/>
    {props.network && <Network {...props} network={props.network}/>}
    <Controls selected={props.selected} network={props.network}/>
    <Effects intensity={0.75} threshold={0.55}/>
  </Canvas>;
}

function Earth() {
  const dots = useMemo(() => landDots(), []);
  const borders = useMemo(() => stateBorders(), []);
  const atmosphere = useMemo(() => new THREE.ShaderMaterial({
    vertexShader: 'varying vec3 vN; void main(){ vN = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: 'varying vec3 vN; void main(){ float i = pow(0.72 - dot(vN, vec3(0.0,0.0,1.0)), 3.2); gl_FragColor = vec4(0.18,0.5,0.95,1.0) * i * 0.8; }',
    blending: THREE.AdditiveBlending, side: THREE.BackSide, transparent: true, depthWrite: false,
  }), []);
  const ocean = useMemo(() => new THREE.ShaderMaterial({
    vertexShader: 'varying vec3 vN; void main(){ vN = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: 'varying vec3 vN; void main(){ float rim = pow(1.0 - max(dot(vN, vec3(0.0,0.0,1.0)),0.0), 2.4); vec3 c = mix(vec3(0.012,0.03,0.065), vec3(0.06,0.22,0.42), rim); gl_FragColor = vec4(c,1.0); }',
  }), []);
  return <group>
    <mesh material={ocean}><sphereGeometry args={[R, 96, 96]}/></mesh>
    <mesh material={atmosphere} scale={1.16}><sphereGeometry args={[R, 64, 64]}/></mesh>
    <points>
      <bufferGeometry><bufferAttribute attach="attributes-position" args={[dots, 3]}/></bufferGeometry>
      <pointsMaterial map={roundDot()} size={0.0085} sizeAttenuation transparent alphaTest={0.05} color="#2f5680" depthWrite={false}/>
    </points>
    <lineSegments>
      <bufferGeometry><bufferAttribute attach="attributes-position" args={[borders, 3]}/></bufferGeometry>
      <lineBasicMaterial color="#5aa9ff" transparent opacity={0.22} depthWrite={false}/>
    </lineSegments>
  </group>;
}

function Network({network, selected, onSelect, highlights, routeLimit, weatherOnly}: Props & {network: Network}) {
  const index = useMemo(() => new Map(network.airports.map(a => [a.code, a])), [network]);
  const positions = useMemo(() => new Map(network.airports.map(a => [a.code, toVec3(a.latitude, a.longitude)])), [network]);
  const [hovered, setHovered] = useState<NetworkAirport | null>(null);

  const visibleRoutes = useMemo(() => network.routes
    .filter(r => !weatherOnly || r.weather / r.flights > 0.15)
    .slice(0, Math.min(routeLimit, BASE_ROUTES)), [network, routeLimit, weatherOnly]);

  const arcs = useMemo(() => visibleRoutes.map(r => {
    const a = positions.get(r.origin), b = positions.get(r.destination);
    return a && b ? {route: r, points: arcPoints(a, b, 40)} : null;
  }).filter((x): x is NonNullable<typeof x> => !!x), [visibleRoutes, positions]);

  const routeGeometry = useMemo(() => {
    const pos: number[] = [], col: number[] = [];
    for (const {route, points} of arcs) {
      const c = weatherColor(route.weather / route.flights);
      const dim = 0.35 + 0.65 * Math.min(1, Math.sqrt(route.flights / 900));
      for (let i = 0; i < points.length - 1; i++) {
        const fade = Math.sin(Math.PI * (i + 0.5) / (points.length - 1)) * 0.6 + 0.4;
        pos.push(points[i].x, points[i].y, points[i].z, points[i + 1].x, points[i + 1].y, points[i + 1].z);
        for (let k = 0; k < 2; k++) col.push(c.r * dim * fade, c.g * dim * fade, c.b * dim * fade);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    return g;
  }, [arcs]);
  useEffect(() => () => routeGeometry.dispose(), [routeGeometry]);

  const selectedArcs = useMemo(() => {
    if (!selected) return [];
    return network.routes.filter(r => r.origin === selected || r.destination === selected).slice(0, 160).map(r => {
      const a = positions.get(r.origin)!, b = positions.get(r.destination)!;
      return a && b ? {route: r, points: arcPoints(a, b, 40)} : null;
    }).filter((x): x is NonNullable<typeof x> => !!x);
  }, [selected, network, positions]);

  const highlightArcs = useMemo(() => highlights.map(h => {
    const a = positions.get(h.origin), b = positions.get(h.destination);
    return a && b ? {...h, points: arcPoints(a, b, 64, 1.35)} : null;
  }).filter((x): x is NonNullable<typeof x> => !!x), [highlights, positions]);

  const moverSources = useMemo(() => {
    const pool = selectedArcs.length ? selectedArcs : arcs.slice(0, 260);
    const total = pool.reduce((s, a) => s + a.route.flights, 0) || 1;
    const out: {points: THREE.Vector3[]; offset: number; speed: number; color: THREE.Color}[] = [];
    for (const a of pool) {
      const n = Math.max(1, Math.round(a.route.flights / total * MOVERS));
      for (let i = 0; i < n && out.length < MOVERS; i++) out.push({points: a.points, offset: Math.random(), speed: 0.05 + Math.random() * 0.05, color: weatherColor(a.route.weather / a.route.flights)});
    }
    return out;
  }, [arcs, selectedArcs]);

  const moverRef = useRef<THREE.Points>(null);
  const moverGeometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(moverSources.length * 3), 3));
    const col = new Float32Array(moverSources.length * 3);
    moverSources.forEach((m, i) => { col[i * 3] = m.color.r * 1.4; col[i * 3 + 1] = m.color.g * 1.4; col[i * 3 + 2] = m.color.b * 1.4; });
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    return g;
  }, [moverSources]);
  useEffect(() => () => moverGeometry.dispose(), [moverGeometry]);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  useFrame(({clock}) => {
    const attr = moverGeometry.getAttribute('position') as THREE.BufferAttribute;
    const t = clock.elapsedTime;
    moverSources.forEach((m, i) => { sampleArc(m.points, (m.offset + t * m.speed) % 1, tmp); attr.setXYZ(i, tmp.x, tmp.y, tmp.z); });
    attr.needsUpdate = true;
  });

  const airportMesh = useRef<THREE.InstancedMesh>(null);
  const maxVolume = useMemo(() => Math.max(...network.airports.map(a => a.inbound + a.outbound)), [network]);
  useEffect(() => {
    const mesh = airportMesh.current;
    if (!mesh) return;
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    network.airports.forEach((a, i) => {
      const p = toVec3(a.latitude, a.longitude, R * 1.003);
      const size = 0.0022 + 0.0095 * Math.sqrt((a.inbound + a.outbound) / maxVolume);
      const emphasis = a.code === selected ? 1.8 : 1;
      q.setFromUnitVectors(new THREE.Vector3(0, 1, 0), p.clone().normalize());
      m.compose(p, q, s.set(size * emphasis, size * 0.4 * emphasis, size * emphasis));
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, a.code === selected ? new THREE.Color('#ffffff') : weatherColor(a.weather_inbound / Math.max(1, a.inbound)).multiplyScalar(1.25));
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [network, selected, maxVolume]);

  const labelled = useMemo(() => {
    const codes = new Set(network.airports.slice(0, 14).map(a => a.code));
    if (selected) codes.add(selected);
    highlights.forEach(h => { codes.add(h.origin); codes.add(h.destination); });
    return [...codes].map(c => index.get(c)).filter((a): a is NetworkAirport => !!a);
  }, [network, selected, highlights, index]);

  const pick = (e: ThreeEvent<PointerEvent | MouseEvent>) => e.instanceId === undefined ? null : network.airports[e.instanceId];

  return <group>
    <lineSegments geometry={routeGeometry}>
      <lineBasicMaterial vertexColors transparent opacity={selected ? 0.08 : 0.26} blending={THREE.AdditiveBlending} depthWrite={false}/>
    </lineSegments>
    {selectedArcs.length > 0 && <Line segments points={selectedArcs.flatMap(a => a.points.slice(0, -1).flatMap((p, i) => [p, a.points[i + 1]]))}
      vertexColors={selectedArcs.flatMap(a => { const c = weatherColor(a.route.weather / a.route.flights); return a.points.slice(0, -1).flatMap(() => [c, c]); })}
      lineWidth={1.6} transparent opacity={0.9} depthWrite={false}/>}
    {highlightArcs.map(h => <group key={`${h.origin}-${h.label}`}>
      <Line points={h.points} color={h.color} lineWidth={3} transparent opacity={0.95} depthWrite={false}/>
      <FlightMarker points={h.points} color={h.color}/>
    </group>)}
    <points ref={moverRef} geometry={moverGeometry}>
      <pointsMaterial map={roundDot()} size={0.012} sizeAttenuation vertexColors transparent depthWrite={false} blending={THREE.AdditiveBlending}/>
    </points>
    <instancedMesh ref={airportMesh} args={[undefined, undefined, network.airports.length]}
      onPointerMove={e => { e.stopPropagation(); setHovered(pick(e)); document.body.style.cursor = 'pointer'; }}
      onPointerOut={() => { setHovered(null); document.body.style.cursor = ''; }}
      onClick={e => { e.stopPropagation(); const a = pick(e); if (a) onSelect(a.code); }}>
      <cylinderGeometry args={[1, 1, 1, 18]}/>
      <meshBasicMaterial toneMapped={false}/>
    </instancedMesh>
    {selected && positions.get(selected) && <Pulse position={positions.get(selected)!}/>}
    {labelled.map(a => <AirportLabel key={a.code} airport={a} position={positions.get(a.code)!} strong={a.code === selected}/>)}
    {hovered && hovered.code !== selected && <Html position={positions.get(hovered.code)!.clone().multiplyScalar(1.02)} center zIndexRange={[9, 0]} style={{pointerEvents: 'none'}}>
      <div className="globe-tooltip"><b>{hovered.code}</b><span>{hovered.name}</span><small>{hovered.inbound.toLocaleString()} delayed arrivals · {Math.round(hovered.weather_inbound / Math.max(1, hovered.inbound) * 100)}% weather</small></div>
    </Html>}
  </group>;
}

function FlightMarker({points, color}: {points: THREE.Vector3[]; color: string}) {
  const ref = useRef<THREE.Mesh>(null);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  const ahead = useMemo(() => new THREE.Vector3(), []);
  useFrame(({clock}) => {
    if (!ref.current) return;
    const t = (clock.elapsedTime * 0.09) % 1;
    sampleArc(points, t, tmp);
    sampleArc(points, Math.min(1, t + 0.01), ahead);
    ref.current.position.copy(tmp);
    ref.current.up.copy(tmp).normalize();
    ref.current.lookAt(ahead);
  });
  return <mesh ref={ref}>
    <coneGeometry args={[0.009, 0.034, 3]} onUpdate={g => g.rotateX(Math.PI / 2)}/>
    <meshBasicMaterial color={color} toneMapped={false}/>
  </mesh>;
}

function Pulse({position}: {position: THREE.Vector3}) {
  const ref = useRef<THREE.Mesh>(null);
  const quaternion = useMemo(() => new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), position.clone().normalize()), [position]);
  useFrame(({clock}) => {
    if (!ref.current) return;
    const s = 1 + (clock.elapsedTime * 0.8 % 1) * 3;
    ref.current.scale.setScalar(s);
    (ref.current.material as THREE.MeshBasicMaterial).opacity = 1 - (clock.elapsedTime * 0.8 % 1);
  });
  return <mesh ref={ref} position={position.clone().multiplyScalar(1.004)} quaternion={quaternion}>
    <ringGeometry args={[0.018, 0.022, 48]}/>
    <meshBasicMaterial color="#ffffff" transparent depthWrite={false} side={THREE.DoubleSide}/>
  </mesh>;
}

function AirportLabel({airport, position, strong}: {airport: NetworkAirport; position: THREE.Vector3; strong: boolean}) {
  const ref = useRef<HTMLDivElement>(null);
  const normal = useMemo(() => position.clone().normalize(), [position]);
  const view = useMemo(() => new THREE.Vector3(), []);
  useFrame(({camera}) => {
    if (!ref.current) return;
    const facing = normal.dot(view.copy(camera.position).normalize());
    ref.current.style.opacity = String(Math.max(0, Math.min(1, (facing - 0.25) * 4)));
  });
  return <Html position={position.clone().multiplyScalar(1.012)} style={{pointerEvents: 'none'}} zIndexRange={[9, 0]}>
    <div ref={ref} className={`globe-label ${strong ? 'strong' : ''}`}>{airport.code}</div>
  </Html>;
}

function Controls({selected, network}: {selected: string | null; network: Network | null}) {
  const ref = useRef<OrbitControlsImpl>(null);
  const {camera} = useThree();
  const target = useRef<THREE.Vector3 | null>(null);
  useEffect(() => {
    const a = selected && network?.airports.find(x => x.code === selected);
    if (a) target.current = toVec3(a.latitude, a.longitude, 2.15);
  }, [selected, network]);
  useFrame((_, delta) => {
    if (!target.current) return;
    const dist = THREE.MathUtils.lerp(camera.position.length(), target.current.length(), Math.min(1, delta * 2.4));
    const dir = camera.position.clone().normalize().lerp(target.current.clone().normalize(), Math.min(1, delta * 2.4)).normalize();
    camera.position.copy(dir.multiplyScalar(dist));
    ref.current?.update();
    if (camera.position.distanceTo(target.current) < 0.01) target.current = null;
  });
  return <OrbitControls ref={ref} enablePan={false} enableDamping dampingFactor={0.08} rotateSpeed={0.45} zoomSpeed={0.6}
    minDistance={1.3} maxDistance={6} autoRotate={!selected} autoRotateSpeed={0.25} onStart={() => { target.current = null; }}/>;
}
