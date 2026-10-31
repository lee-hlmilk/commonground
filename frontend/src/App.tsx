import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, ChevronDown, FileText, Globe2, Info, LayoutGrid, Loader2, Play, RefreshCw, X } from 'lucide-react';
import { api } from './api';
import type { Flight, Health, Message, Network, Participant, Run, Scenario, ScenarioSummary } from './types';
import { FLIGHT_COLORS, TYPE_COLORS } from './lib/format';
import { horizonOf } from './lib/sim';
import { simClock } from './lib/clock';
import { AirportPanel, GlobeOverview } from './components/GlobePanels';
import { CommsPanel, DecisionPanel, FlightCard, type ViewPlan } from './components/OpsPanels';
import Timeline from './components/Timeline';
import { Evidence } from './components/Evidence';
import type { Beam, CameraPreset } from './airport/AirportScene';

const GlobeScene = lazy(() => import('./globe/GlobeScene'));
const AirportScene = lazy(() => import('./airport/AirportScene'));

type SavedRun = {id: string; name: string; created_at: string; mode: string; status: string};

export default function App() {
  const [view, setView] = useState<'globe' | 'ops'>('globe');
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [selected, setSelected] = useState('atl-three-flight-clash');
  const [loaded, setLoaded] = useState<Scenario | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [network, setNetwork] = useState<Network | null>(null);
  const [savedRuns, setSavedRuns] = useState<SavedRun[]>([]);
  const [replaySource, setReplaySource] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [airport, setAirport] = useState<string | null>(null);
  const [routeLimit, setRouteLimit] = useState(700);
  const [weatherOnly, setWeatherOnly] = useState(false);
  const [selectedFlight, setSelectedFlight] = useState<string | null>(null);
  const [stand, setStand] = useState('');
  const [viewPlan, setViewPlan] = useState<ViewPlan>({kind: 'proposal'});
  const [beams, setBeams] = useState<Beam[]>([]);
  const [left, setLeft] = useState(true);
  const [right, setRight] = useState(true);
  const [preset, setPreset] = useState<{name: CameraPreset; nonce: number}>({name: 'overview', nonce: 0});
  const [drawer, setDrawer] = useState<{flight: Flight | null} | null>(null);
  const [narrow, setNarrow] = useState(() => window.innerWidth <= 900);
  const sourceRef = useRef<EventSource | null>(null);
  const seen = useRef(new Set<string>());
  const timers = useRef<number[]>([]);

  const scenario = run?.scenario ?? loaded;
  const recorded = run?.mode === 'recorded_replay';
  const active = run?.status === 'negotiating';
  const providerName = health?.provider_label ?? (health?.provider === 'jev' ? 'Jev' : 'Claude');
  const providerConfigured = health?.provider_configured ?? health?.claude_configured ?? false;
  const providerKey = health?.provider === 'jev' ? 'JEV_API_KEY or TYPESAFE_API_KEY' : 'ANTHROPIC_API_KEY';

  useEffect(() => {
    Promise.all([api<{scenarios: ScenarioSummary[]}>('/scenarios'), api<Health>('/health'), api<Record<string, unknown>>('/data/summary')])
      .then(([list, h, s]) => { setScenarios(list.scenarios); setHealth(h); setSummary(s); }).catch(e => setError(e.message));
    api<Network>('/network?limit=5000').then(setNetwork).catch(e => setError(`Route network unavailable: ${e.message}`));
    const resize = () => setNarrow(window.innerWidth <= 900);
    window.addEventListener('resize', resize);
    return () => { sourceRef.current?.close(); timers.current.forEach(clearTimeout); window.removeEventListener('resize', resize); };
  }, []);

  useEffect(() => {
    let live = true;
    setLoaded(null); setRun(null); sourceRef.current?.close(); setSelectedFlight(null); setPreset(x => ({name: 'overview', nonce: x.nonce + 1}));
    api<Scenario>(`/scenarios/${encodeURIComponent(selected)}`).then(s => { if (live) { setLoaded(s); setStand(s.resources.stands[0]?.id ?? ''); } }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [selected]);

  useEffect(() => {
    if (!run?.id) return;
    const source = new EventSource(`/api/runs/${run.id}/events`);
    sourceRef.current = source;
    source.addEventListener('update', e => { try { setRun(JSON.parse((e as MessageEvent).data)); } catch { /* the next event can recover */ } });
    return () => source.close();
  }, [run?.id]);

  useEffect(() => {
    api<{runs: SavedRun[]}>('/runs').then(r => setSavedRuns(r.runs.filter(x => x.status !== 'negotiating' && x.mode !== 'recorded_replay'))).catch(() => {});
  }, [run?.id, run?.status]);

  const participants: Participant[] = useMemo(() => run?.participants ?? (scenario ? [
    {id: 'atc', name: 'ATC traffic coordinator', role: 'atc'},
    ...scenario.flights.map(f => ({id: `pilot-${f.id}`, name: `${f.callsign} captain`, role: 'pilot', flight_id: f.id})),
    {id: 'ground', name: 'Ground turnaround coordinator', role: 'ground'},
  ] : []), [run?.participants, scenario]);

  const fire = useCallback((messages: Message[], gap: number) => {
    messages.forEach((m, i) => {
      timers.current.push(window.setTimeout(() => setBeams(b => [...b.slice(-14), {id: `${m.id}-${performance.now()}`, from: m.sender, to: m.recipients?.length ? m.recipients : ['atc'], color: TYPE_COLORS[m.type] ?? '#5aa9ff'}]), i * gap));
    });
  }, []);

  useEffect(() => {
    const fresh = (run?.messages ?? []).filter(m => !seen.current.has(m.id));
    fresh.forEach(m => seen.current.add(m.id));
    if (fresh.length && view === 'ops') fire(fresh, 420);
  }, [run?.messages, view, fire]);

  const plan = useMemo(() => {
    if (!run) return null;
    if (viewPlan.kind === 'baseline') return run.baseline ?? run.proposal;
    if (viewPlan.kind === 'candidate') return run.candidates.find(c => c.id === viewPlan.id) ?? run.proposal;
    return run.proposal;
  }, [run, viewPlan]);

  const horizon = useMemo(() => scenario ? horizonOf(scenario, [run?.proposal, run?.baseline, ...(run?.candidates ?? [])]) : 120, [scenario, run?.proposal, run?.baseline, run?.candidates]);
  useEffect(() => { simClock.setHorizon(horizon); }, [horizon]);
  useEffect(() => {
    simClock.pause();
    simClock.set(0);
    if (plan && view === 'ops') simClock.play();
  }, [plan?.id, scenario?.id, scenario?.version, view]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (view !== 'ops' || (e.target as HTMLElement)?.closest('input, select, textarea, button')) return;
      if (e.code === 'Space') { e.preventDefault(); simClock.toggle(); }
      if (e.key === 'Escape') { setSelectedFlight(null); setDrawer(null); }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [view]);

  async function action(fn: () => Promise<void>) { setBusy(true); setError(''); try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); } }
  function start(mode: string) {
    if (!scenario) return;
    void action(async () => {
      const next = await api<Run>('/runs', {scenario_id: scenario.id, mode, ...(mode === 'replay' && replaySource ? {replay_source_id: replaySource} : {})});
      seen.current = new Set(); setViewPlan({kind: 'proposal'}); setRun(next); setView('ops');
    });
  }
  const decide = (d: 'approve' | 'reject') => run?.proposal && void action(async () => setRun(await api<Run>(`/runs/${run.id}/decision`, {proposal_id: run.proposal!.id, scenario_version: run.scenario.version, decision: d})));
  const disrupt = (body: unknown) => run && void action(async () => { setViewPlan({kind: 'proposal'}); setRun(await api<Run>(`/runs/${run.id}/disruptions`, body)); });
  const openScenario = (id: string) => { setRun(null); setSelected(id); setView('ops'); };

  const highlights = useMemo(() => scenario ? scenario.flights.map((f, i) => ({origin: f.origin.code, destination: f.destination.code, color: FLIGHT_COLORS[i % FLIGHT_COLORS.length], label: f.callsign})) : [], [scenario]);
  const globeInsets = useMemo(() => narrow ? {left: 0, right: 0, top: 0, bottom: 0} : {left: 360, right: airport ? 400 : 0, top: 66, bottom: 90}, [narrow, airport]);
  const opsInsets = useMemo(() => narrow ? {left: 0, right: 0, top: 0, bottom: 0} : {left: left ? 384 : 0, right: right ? 392 : 0, top: 180, bottom: 250}, [narrow, left, right]);
  const lastScenario = useRef<Scenario | null>(null);
  if (scenario && view === 'ops') lastScenario.current = scenario;
  const opsScenario = view === 'ops' ? scenario ?? lastScenario.current : lastScenario.current;
  const flightIndex = scenario && selectedFlight ? scenario.flights.findIndex(f => f.id === selectedFlight) : -1;

  return <div className={`app view-${view}`}>
    <header className="topbar glass">
      <a className="brand" href="/" aria-label="Common Ground home"><span className="mark"/><span>common<b>ground</b></span></a>
      <nav className="view-tabs" aria-label="Views">
        <button className={view === 'globe' ? 'active' : ''} onClick={() => setView('globe')}><Globe2 size={15}/> Network globe</button>
        <button className={view === 'ops' ? 'active' : ''} onClick={() => setView('ops')} disabled={!scenario}><LayoutGrid size={15}/> Recovery room</button>
      </nav>
      {view === 'ops' && <div className="run-controls">
        <div className="select"><select aria-label="Coordination scenario" value={recorded && run ? run.scenario.id : selected} onChange={e => { setRun(null); setSelected(e.target.value); }} disabled={busy || active}>
          {scenarios.length ? scenarios.map(s => <option value={s.id} key={s.id}>{s.name}</option>) : <option value={selected}>Loading…</option>}
        </select><ChevronDown size={14}/></div>
        <div className="select small"><select aria-label="Replay source" value={replaySource} onChange={e => setReplaySource(e.target.value)} disabled={busy || active}>
          <option value="">Authored illustration</option>
          {savedRuns.map(r => <option value={r.id} key={r.id}>{r.name} · {r.mode} · {new Date(r.created_at).toLocaleTimeString()}</option>)}
        </select><ChevronDown size={14}/></div>
        <button className="btn" disabled={busy || active || !scenario} onClick={() => start('replay')} title="Labelled replay; no model calls"><RefreshCw size={14}/>{replaySource ? 'Recorded replay' : 'Illustrative replay'}</button>
        <button className="btn primary" disabled={busy || active || !scenario || !providerConfigured} onClick={() => start('live')} title={providerConfigured ? `Start independent ${providerName} participants` : `Add ${providerKey} to the backend .env`}>
          {busy || active ? <Loader2 size={14} className="spin"/> : <Play size={13} fill="currentColor"/>}{active ? 'Coordinating…' : 'Coordinate live'}
        </button>
      </div>}
      <div className="top-right">
        <span className={`key-state ${providerConfigured ? 'ok' : ''}`} title={providerConfigured ? `${providerName} key present; access is checked on a live run` : `Live coordination needs ${providerKey} in the backend .env. Replay and fallback work now.`}><i/>{providerName} {providerConfigured ? 'ready' : 'key needed'}{health?.provider === 'jev' ? ` · Claude ${health.fallback_configured ? 'fallback ready' : 'fallback off'}` : ''}</span>
        <button className="icon-btn" onClick={() => setDrawer({flight: null})} aria-label="Evidence & assumptions" title="Evidence & assumptions"><FileText size={16}/></button>
        <button className="icon-btn" disabled={!run} onClick={() => run && window.open(`/api/runs/${run.id}/export`, '_blank', 'noopener')} aria-label="Export record" title="Export audit record"><ArrowDownToLine size={16}/></button>
      </div>
    </header>

    {error && <div className="toast" role="alert"><Info size={16}/><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><X size={14}/></button></div>}

    <div className="scenes">
      <div className={`scene ${view === 'globe' ? 'on' : 'off'}`} aria-hidden={view !== 'globe'}><Suspense fallback={<div className="scene-loading"><Loader2 className="spin"/> Building globe…</div>}>
        <GlobeScene active={view === 'globe'} network={network} selected={airport} onSelect={setAirport} highlights={highlights} routeLimit={routeLimit} weatherOnly={weatherOnly} insets={globeInsets}/>
      </Suspense></div>
      {opsScenario && <div className={`scene ${view === 'ops' ? 'on' : 'off'}`} aria-hidden={view !== 'ops'}><Suspense fallback={<div className="scene-loading"><Loader2 className="spin"/> Building airport…</div>}>
        <AirportScene active={view === 'ops'} scenario={opsScenario} plan={scenario ? plan : null} beams={beams} onBeamDone={id => setBeams(b => b.filter(x => x.id !== id))}
          selectedFlight={selectedFlight} onSelectFlight={setSelectedFlight} selectedStand={stand} onSelectStand={setStand} preset={preset.name} presetNonce={preset.nonce} insets={opsInsets}/>
      </Suspense></div>}
    </div>

    {view === 'globe' && <main className="stage">
      <GlobeOverview network={network} summary={summary} routeLimit={routeLimit} onRouteLimit={setRouteLimit} weatherOnly={weatherOnly} onWeatherOnly={setWeatherOnly} onSelect={setAirport} selected={airport}/>
      {airport && <AirportPanel code={airport} network={network} scenarios={scenarios} onClose={() => setAirport(null)} onOpenScenario={openScenario}
        onBuilt={s => { setScenarios(p => [...p.filter(x => x.id !== s.id), {id: s.id, name: s.name, description: s.description, airport: s.airport, flight_count: s.flights.length, date: s.date}]); setRun(null); setSelected(s.id); setLoaded(s); setView('ops'); }}/>}
      <div className="globe-cta">
        {scenario && <button className="btn primary lg" onClick={() => setView('ops')}>Open {scenario.airport.code} recovery room <span>{scenario.name}</span><ArrowRight size={16}/></button>}
        <small>Drag to rotate · scroll to zoom · click an airport · highlighted arcs are the scenario’s inbound flights</small>
      </div>
    </main>}

    {view === 'ops' && scenario && <main className="stage ops">
      <div className="scene-head">
        <div className="scenario-title"><span className="eyebrow">{scenario.airport.code} · {scenario.date} · clock {scenario.clock_start}</span><h1>{scenario.name}</h1><p>{scenario.description}</p></div>
        <div className="camera-bar" role="group" aria-label="Camera">
          {(['overview', 'airspace', 'apron', 'runway'] as CameraPreset[]).map(p => <button key={p} className={preset.name === p && !selectedFlight ? 'active' : ''} onClick={() => { setSelectedFlight(null); setPreset(x => ({name: p, nonce: x.nonce + 1})); }}>{p}</button>)}
          {selectedFlight && <span className="following">following {scenario.flights[flightIndex]?.callsign}</span>}
        </div>
        {plan && plan !== run?.proposal && <div className="preview-flag">Previewing {plan.kind === 'fallback' ? 'fixed-rule baseline' : 'an alternative option'} — not the plan awaiting approval <button onClick={() => setViewPlan({kind: 'proposal'})}>Show recommended</button></div>}
        {!run && <div className="preview-flag info">No coordination run yet — aircraft hold at reference positions. Start an illustrative replay or live coordination.</div>}
      </div>
      <div className="legend-chips"><span><i className="c-hold"/>holding / airborne wait</span><span><i className="c-surf"/>surface holding</span><span><i className="c-closed"/>closed / unavailable</span><span><i className="c-free"/>free stand</span><span><i className="c-msg"/>agent transmission</span></div>
      <CommsPanel run={run} participants={participants} scenario={scenario} viewPlan={viewPlan} onViewPlan={setViewPlan} onReplayBeams={() => run && fire(run.messages, 650)}
        onBeamMessage={m => fire([m], 0)} onStart={start} busy={busy} collapsed={!left} onToggle={() => setLeft(v => !v)}/>
      <DecisionPanel run={run} scenario={scenario} viewPlan={viewPlan} onViewPlan={setViewPlan} busy={busy} onDecision={decide} onFallback={() => start('fallback')}
        onDisrupt={disrupt} stand={stand} onStand={setStand} collapsed={!right} onToggle={() => setRight(v => !v)}/>
      {selectedFlight && flightIndex >= 0 && <FlightCard flight={scenario.flights[flightIndex]} index={flightIndex} plan={plan} scenario={scenario} onClose={() => setSelectedFlight(null)} onEvidence={() => setDrawer({flight: scenario.flights[flightIndex]})}/>}
      <Timeline scenario={scenario} plan={plan} horizon={horizon} selectedFlight={selectedFlight} onSelectFlight={setSelectedFlight}/>
    </main>}

    {view === 'ops' && !scenario && <div className="scene-loading full"><Loader2 className="spin"/> Loading scenario…</div>}

    {drawer && <div className="drawer-backdrop" onMouseDown={() => setDrawer(null)}>
      <aside className="drawer glass" role="dialog" aria-modal="true" aria-label="Evidence and assumptions" onMouseDown={e => e.stopPropagation()}>
        <header className="panel-head"><div><span className="eyebrow">CHECK THE EVIDENCE</span><h2>{drawer.flight?.callsign ?? 'Facts & assumptions'}</h2></div><button className="icon-btn ghost" onClick={() => setDrawer(null)} aria-label="Close panel"><X size={18}/></button></header>
        <div className="panel-scroll"><Evidence scenario={scenario} flight={drawer.flight} summary={summary}/></div>
      </aside>
    </div>}
  </div>;
}
