import { useEffect, useMemo, useRef } from 'react';
import { Pause, Play, RotateCcw } from 'lucide-react';
import type { Plan, Scenario } from '../types';
import { simClock, useSimClock } from '../lib/clock';
import { clock, FLIGHT_COLORS } from '../lib/format';
import { schedules, standStatus, stateAt, PHASE_LABEL } from '../lib/sim';

type Props = {scenario: Scenario; plan: Plan | null; horizon: number; selectedFlight: string | null; onSelectFlight: (id: string) => void};

const SPEEDS = [1, 2, 5, 10];

export default function Timeline({scenario, plan, horizon, selectedFlight, onSelectFlight}: Props) {
  const snap = useSimClock(150);
  const scheds = useMemo(() => schedules(scenario, plan), [scenario, plan]);
  const track = useRef<HTMLDivElement>(null);
  const cursor = useRef<HTMLDivElement>(null);
  const pct = (v: number) => `${Math.max(0, Math.min(100, v / horizon * 100))}%`;
  const width = (a: number, b: number) => `${Math.max(0, (Math.min(horizon, b) - Math.max(0, a)) / horizon * 100)}%`;
  const closures = scenario.resources.runways.flatMap(r => r.closures.map(c => ({...c, runway: r.id})));
  const ticks = useMemo(() => { const step = horizon > 150 ? 30 : 15; return Array.from({length: Math.floor(horizon / step) + 1}, (_, i) => i * step); }, [horizon]);

  useEffect(() => simClock.subscribe(() => { if (cursor.current) cursor.current.style.left = pct(simClock.t); }));
  useEffect(() => { if (cursor.current) cursor.current.style.left = pct(simClock.t); });

  function seek(clientX: number) {
    const el = track.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    simClock.set((clientX - box.left) / box.width * horizon);
  }
  function down(e: React.PointerEvent) {
    if (e.button !== 0) return;
    simClock.pause();
    seek(e.clientX);
    const move = (ev: PointerEvent) => seek(ev.clientX);
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  const now = snap.t;
  return <section className="timeline glass" aria-label="Simulation timeline">
    <div className="tl-controls">
      <button className="icon-btn play" onClick={() => simClock.toggle()} aria-label={snap.playing ? 'Pause simulation' : 'Play simulation'}>{snap.playing ? <Pause size={16}/> : <Play size={16} fill="currentColor"/>}</button>
      <button className="icon-btn" onClick={() => { simClock.pause(); simClock.set(0); }} aria-label="Restart simulation"><RotateCcw size={15}/></button>
      <div className="tl-clock"><b>{clock(scenario.clock_start, now)}</b><span>T+{Math.floor(now)} min · {scenario.airport.code} local</span></div>
      <div className="speed-group" role="group" aria-label="Playback speed">{SPEEDS.map(s => <button key={s} className={snap.speed === s ? 'active' : ''} onClick={() => simClock.setSpeed(s)}>{s}×</button>)}</div>
      <div className="tl-status">{scheds.map((s, i) => { const st = stateAt(now, s); const f = scenario.flights[i]; return <button key={s.flightId} className={`tl-chip ${selectedFlight === s.flightId ? 'active' : ''}`} style={{'--c': FLIGHT_COLORS[i % FLIGHT_COLORS.length]} as React.CSSProperties} onClick={() => onSelectFlight(s.flightId)}><i/>{f.callsign}<span>{s.planned || st.phase === 'enroute' ? PHASE_LABEL[st.phase] : 'Holding · no plan'}</span></button>; })}</div>
    </div>
    <div className="tl-grid">
      <div className="tl-labels">
        <div className="tl-axis-label">{plan ? (plan.kind === 'fallback' ? 'FIXED RULE' : 'PLAN') : 'REFERENCE'}</div>
        {scenario.flights.map((f, i) => <button key={f.id} className={`tl-label ${selectedFlight === f.id ? 'active' : ''}`} onClick={() => onSelectFlight(f.id)}><i style={{background: FLIGHT_COLORS[i % FLIGHT_COLORS.length]}}/>{f.callsign}<small>{f.origin.code}</small></button>)}
        {scenario.resources.runways.map(r => <div key={r.id} className="tl-label res">{r.id}</div>)}
        {scenario.resources.stands.map(s => <div key={s.id} className="tl-label res">Stand {s.id}</div>)}
      </div>
      <div className="tl-track" ref={track} onPointerDown={down}>
        <div className="tl-axis">{ticks.map(t => <span key={t} style={{left: pct(t)}}>{clock(scenario.clock_start, t)}</span>)}</div>
        <div className="tl-gridlines">{ticks.map(t => <i key={t} style={{left: pct(t)}}/>)}</div>
        {closures.map((c, i) => <div key={i} className="tl-closure" style={{left: pct(c.start), width: width(c.start, c.end)}} title={`${c.runway} closed ${clock(scenario.clock_start, c.start)}–${clock(scenario.clock_start, c.end)}`}/>)}
        {scenario.flights.map((f, i) => {
          const s = scheds[i], color = FLIGHT_COLORS[i % FLIGHT_COLORS.length];
          return <div key={f.id} className={`tl-row ${selectedFlight === f.id ? 'active' : ''}`} style={{'--c': color} as React.CSSProperties}>
            <div className="tl-window" style={{left: pct(f.reference_arrival), width: width(f.reference_arrival, f.latest_arrival)}} title={`Permitted arrival window ${clock(scenario.clock_start, f.reference_arrival)}–${clock(scenario.clock_start, f.latest_arrival)}`}/>
            {s.planned ? <>
              {s.airborneWait > 0 && <div className="tl-seg hold" style={{left: pct(s.reference), width: width(s.reference, s.arrival)}} title={`${s.airborneWait} min simulated airborne holding`}><span>+{s.airborneWait}</span></div>}
              <div className="tl-seg taxi" style={{left: pct(s.arrival), width: width(s.arrival, s.arrival + f.taxi_in)}}/>
              {s.surfaceWait > 0 && <div className="tl-seg surface" style={{left: pct(s.arrival + f.taxi_in), width: width(s.arrival + f.taxi_in, s.inBlock)}} title={`${s.surfaceWait} min authorised surface holding`}/>}
              <div className="tl-seg turn" style={{left: pct(s.inBlock), width: width(s.inBlock, s.offBlock)}} title={`Turnaround at ${s.stand} ${clock(scenario.clock_start, s.inBlock)}–${clock(scenario.clock_start, s.offBlock)}`}><span>{s.stand}</span></div>
              <div className="tl-seg taxi" style={{left: pct(s.offBlock), width: width(s.offBlock, s.departure)}}/>
              <i className="tl-mark arr" style={{left: pct(s.arrival)}} title={`Landing ${clock(scenario.clock_start, s.arrival)}`}/>
              <i className="tl-mark dep" style={{left: pct(s.departure)}} title={`Departure ${clock(scenario.clock_start, s.departure)}`}/>
            </> : <>
              <i className="tl-mark ref" style={{left: pct(f.reference_arrival)}} title="Reference arrival estimate"/>
              <div className="tl-pending" style={{left: pct(f.reference_arrival)}}>awaiting coordinated plan</div>
            </>}
          </div>;
        })}
        {scenario.resources.runways.map(r => <div key={r.id} className="tl-row res">
          {r.closures.map((c, i) => <div key={i} className="tl-seg closed" style={{left: pct(c.start), width: width(c.start, c.end)}}><span>closed</span></div>)}
          {scheds.flatMap((s, i) => s.runway === r.id ? [s.arrival, s.departure].map((m, k) => <i key={`${i}${k}`} className="tl-move" style={{left: pct(m), width: width(m, m + r.separation), background: FLIGHT_COLORS[i % FLIGHT_COLORS.length]}} title={`${scenario.flights[i].callsign} ${k ? 'departure' : 'arrival'} + ${r.separation} min separation`}/>) : [])}
        </div>)}
        {scenario.resources.stands.map(st => <div key={st.id} className="tl-row res">
          {st.available_from > 0 && <div className="tl-seg closed" style={{left: 0, width: width(0, st.available_from)}}><span>unavailable</span></div>}
          {scheds.map((s, i) => s.stand === st.id ? <div key={i} className="tl-seg occ" style={{left: pct(s.inBlock), width: width(s.inBlock, s.offBlock), background: FLIGHT_COLORS[i % FLIGHT_COLORS.length]}}><span>{scenario.flights[i].callsign}</span></div> : null)}
          <StandNow scenario={scenario} standId={st.id} t={now} scheds={scheds}/>
        </div>)}
        <div className="tl-cursor" ref={cursor}><span/></div>
      </div>
    </div>
  </section>;
}

function StandNow({scenario, standId, t, scheds}: {scenario: Scenario; standId: string; t: number; scheds: ReturnType<typeof schedules>}) {
  const st = standStatus(scenario, scheds, standId, t);
  return <span className={`tl-now ${st.state}`}>{st.state}</span>;
}
