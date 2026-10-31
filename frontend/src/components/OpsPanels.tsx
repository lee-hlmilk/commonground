import { useState } from 'react';
import { ArrowRight, Check, CheckCircle2, Clock3, Eye, FileText, GitBranch, Headphones, Loader2, MessageSquare, Plane, Radio, ShieldCheck, Sparkles, X, Zap } from 'lucide-react';
import type { Flight, Message, Participant, Plan, Run, Scenario } from '../types';
import { airlineName, clock, FLIGHT_COLORS, histTime, ROLE_COLORS, ROLE_LABEL, STATUS, TYPE_COLORS } from '../lib/format';
import { PHASE_LABEL, scheduleFor, stateAt } from '../lib/sim';
import { useSimClock } from '../lib/clock';
import { CheckRow, DecisionDetails } from './Evidence';

export type ViewPlan = {kind: 'proposal'} | {kind: 'baseline'} | {kind: 'candidate'; id: string};

function RoleIcon({role, size = 15}: {role: string; size?: number}) {
  return role === 'atc' ? <Radio size={size}/> : role === 'ground' ? <Headphones size={size}/> : <Plane size={size}/>;
}

function sourceOf(m: Message) {
  if (m.fallback?.to === 'claude') return 'Claude · fallback';
  if (m.provider === 'jev') return 'Jev';
  if (m.provider === 'claude') return 'Claude';
  if ((m as {fixture?: boolean}).fixture) return 'Authored fixture';
  return m.provider ?? 'Rule';
}

export function CommsPanel({run, participants, scenario, viewPlan, onViewPlan, onReplayBeams, onBeamMessage, onStart, busy, collapsed, onToggle}: {
  run: Run | null; participants: Participant[]; scenario: Scenario; viewPlan: ViewPlan; onViewPlan: (v: ViewPlan) => void;
  onReplayBeams: () => void; onBeamMessage: (m: Message) => void; onStart: (mode: string) => void; busy: boolean; collapsed: boolean; onToggle: () => void;
}) {
  const [tab, setTab] = useState<'comms' | 'checks' | 'candidates'>('comms');
  const messages = run?.messages ?? [];
  const checks = run?.proposal?.checks?.length ? run.proposal.checks : run?.checks ?? [];
  const passed = checks.filter(c => c.passed).length;
  const active = run?.status === 'negotiating';
  const rounds = [...new Set(messages.map(m => `${m.scenario_version}:${m.round}`))];
  if (collapsed) return <button className="panel-tab left glass" onClick={onToggle}><MessageSquare size={16}/> Agents <span>{messages.length}</span></button>;
  return <aside className="ops-panel left glass" aria-label="Agent coordination">
    <header className="panel-head">
      <div><span className="eyebrow">MULTI-AGENT COORDINATION</span><h2>Independent voices</h2></div>
      <button className="icon-btn ghost" onClick={onToggle} aria-label="Collapse agent panel"><X size={16}/></button>
    </header>
    <div className="round-meter" aria-label="Negotiation rounds">
      {[1, 2].map(r => <div key={r} className={`round ${run && run.round >= r ? 'reached' : ''} ${run?.round === r && active ? 'live' : ''}`}><b>Round {r}</b><span>{r === 1 ? 'Proposals & limits' : 'Commit to one plan'}</span></div>)}
      <div className="round-note">Hard limit: 2 rounds · one response per participant per round</div>
    </div>
    <div className="agent-row">{participants.map((p, i) => {
      const last = messages.filter(m => m.sender === p.id).at(-1);
      const state = last?.type === 'ACCEPT' ? 'committed' : last?.type === 'UNABLE' ? 'limit' : active ? 'assessing' : last ? 'responded' : 'ready';
      const color = p.role === 'pilot' ? FLIGHT_COLORS[(participants.filter(x => x.role === 'pilot').indexOf(p)) % FLIGHT_COLORS.length] : ROLE_COLORS[p.role];
      return <div key={p.id} className={`agent ${state}`} style={{'--c': color} as React.CSSProperties} title={`${ROLE_LABEL[p.role] ?? p.role}: ${p.name}`}>
        <span className="agent-icon"><RoleIcon role={p.role}/></span>
        <b>{p.role === 'pilot' ? p.name.split(' ')[0] : p.role === 'atc' ? 'ATC' : 'Ground'}</b>
        <small>{state === 'committed' ? 'Committed' : state === 'limit' ? 'Limitation' : state === 'assessing' ? 'Assessing' : state === 'responded' ? 'Responded' : 'Ready'}</small>
        {i < participants.length && <em/>}
      </div>;
    })}</div>
    <div className="tabs">
      <button className={tab === 'comms' ? 'active' : ''} onClick={() => setTab('comms')}>Transmissions <span>{messages.length}</span></button>
      <button className={tab === 'checks' ? 'active' : ''} onClick={() => setTab('checks')}>Checks <span className={checks.length && passed < checks.length ? 'warn' : ''}>{checks.length ? `${passed}/${checks.length}` : 0}</span></button>
      <button className={tab === 'candidates' ? 'active' : ''} onClick={() => setTab('candidates')}>Options <span>{run?.candidates?.length ?? 0}</span></button>
    </div>
    <div className="panel-scroll">
      {tab === 'comms' && <>
        {messages.length > 0 && <button className="text-btn replay" onClick={onReplayBeams}><Sparkles size={13}/> Replay transmissions in 3D</button>}
        {messages.length ? rounds.map(key => {
          const [version, round] = key.split(':');
          return <div key={key} className="round-group">
            <div className="round-divider"><span>Round {round}</span><em>scenario v{version}</em></div>
            {messages.filter(m => `${m.scenario_version}:${m.round}` === key).map(m => {
              const p = participants.find(x => x.id === m.sender);
              return <article key={m.id} className="msg" onClick={() => onBeamMessage(m)} style={{'--c': ROLE_COLORS[p?.role ?? 'atc']} as React.CSSProperties}>
                <div className="msg-avatar"><RoleIcon role={p?.role ?? ''} size={14}/></div>
                <div className="msg-body">
                  <div className="msg-head"><strong>{p?.name ?? m.sender}</strong><span className="type" style={{color: TYPE_COLORS[m.type] ?? '#cbd5e1', borderColor: TYPE_COLORS[m.type] ?? '#475569'}}>{m.type}</span></div>
                  <p>{m.statement}</p>
                  <div className="msg-meta">
                    {m.recipients?.length > 0 && <span>→ {m.recipients.map(id => participants.find(x => x.id === id)?.name.split(' ')[0] ?? id).join(', ')}</span>}
                    {m.evidence_refs?.length ? <span><FileText size={10}/> {m.evidence_refs.length} evidence</span> : null}
                    <span className="src">{sourceOf(m)}</span>
                  </div>
                  <DecisionDetails message={m}/>
                </div>
              </article>;
            })}
          </div>;
        }) : <div className="empty">
          <MessageSquare size={26}/>
          <h3>No transmissions yet</h3>
          <p>ATC, each captain and ground operations reason in separate contexts, exchange structured proposals and limits, then commit to one checked plan.</p>
          <button className="btn primary sm" disabled={busy} onClick={() => onStart('replay')}>Run illustrative replay <ArrowRight size={13}/></button>
        </div>}
        {active && <div className="working"><Loader2 size={14} className="spin"/> Participants are assessing the situation…</div>}
      </>}
      {tab === 'checks' && (checks.length ? <div className="checks">
        <div className={`check-summary ${passed === checks.length ? 'ok' : 'bad'}`}><ShieldCheck size={16}/>{passed === checks.length ? `All ${checks.length} independent checks pass` : `${checks.length - passed} of ${checks.length} checks unresolved`}</div>
        {[...checks].sort((a, b) => Number(a.passed) - Number(b.passed)).map((c, i) => <CheckRow key={`${c.id}-${i}`} check={c}/>)}
      </div> : <div className="empty"><ShieldCheck size={24}/><p>Checks appear once a plan has been evaluated. No unchecked plan can be approved.</p></div>)}
      {tab === 'candidates' && <Candidates run={run} scenario={scenario} viewPlan={viewPlan} onViewPlan={onViewPlan}/>}
    </div>
  </aside>;
}

function Candidates({run, scenario, viewPlan, onViewPlan}: {run: Run | null; scenario: Scenario; viewPlan: ViewPlan; onViewPlan: (v: ViewPlan) => void}) {
  if (!run?.candidates?.length && !run?.baseline) return <div className="empty"><GitBranch size={24}/><p>The OR-Tools solver generates up to twelve checked options. They appear here once a run starts.</p></div>;
  const rows: {plan: Plan; view: ViewPlan; label: string}[] = [
    ...(run.baseline ? [{plan: run.baseline, view: {kind: 'baseline'} as ViewPlan, label: 'Fixed-rule FCFS baseline'}] : []),
    ...run.candidates.map((p, i) => ({plan: p, view: (p.id === run.proposal?.id ? {kind: 'proposal'} : {kind: 'candidate', id: p.id}) as ViewPlan, label: `Option ${i + 1}${p.id === run.proposal?.id ? ' · selected' : ''}`})),
  ];
  const isActive = (v: ViewPlan) => v.kind === viewPlan.kind && (v.kind !== 'candidate' || (viewPlan.kind === 'candidate' && viewPlan.id === v.id));
  return <div className="candidates">
    <p className="hint">Preview any option in the 3D scene. Only the plan the participants committed to can be approved.</p>
    {rows.map(r => <button key={r.plan.id + r.label} className={`cand ${isActive(r.view) ? 'active' : ''} ${r.plan.kind === 'fallback' ? 'base' : ''}`} onClick={() => onViewPlan(r.view)}>
      <div className="cand-head"><b>{r.label}</b>{isActive(r.view) && <Eye size={13}/>}</div>
      <div className="cand-stands">{r.plan.assignments.map(a => { const i = scenario.flights.findIndex(f => f.id === a.flight_id); return <span key={a.flight_id} style={{color: FLIGHT_COLORS[i % FLIGHT_COLORS.length]}}>{scenario.flights[i]?.callsign} {a.stand_id} {clock(scenario.clock_start, a.arrival)}</span>; })}</div>
      <div className="cand-metrics"><span>air {r.plan.metrics.airborne_wait ?? '—'}</span><span>surface {r.plan.metrics.surface_wait ?? '—'}</span><span>delay {r.plan.metrics.total_delay ?? '—'}</span></div>
    </button>)}
  </div>;
}

const METRICS: [string, string][] = [['airborne_wait', 'Added airborne wait'], ['surface_wait', 'Surface waiting'], ['total_delay', 'Departure delay vs target'], ['overtime', 'Staff overtime'], ['connection_exposure', 'Connection exposure']];

export function DecisionPanel({run, scenario, viewPlan, onViewPlan, busy, onDecision, onFallback, onDisrupt, stand, onStand, collapsed, onToggle}: {
  run: Run | null; scenario: Scenario; viewPlan: ViewPlan; onViewPlan: (v: ViewPlan) => void; busy: boolean;
  onDecision: (d: 'approve' | 'reject') => void; onFallback: () => void; onDisrupt: (body: unknown) => void; stand: string; onStand: (s: string) => void;
  collapsed: boolean; onToggle: () => void;
}) {
  if (collapsed) return <button className="panel-tab right glass" onClick={onToggle}><ShieldCheck size={16}/> Decision</button>;
  const recorded = run?.mode === 'recorded_replay';
  const active = run?.status === 'negotiating';
  const canDecide = !!run?.proposal && !recorded && ['awaiting_approval', 'fallback'].includes(run.status);
  const p = run?.proposal;
  const failed = p?.checks?.filter(c => !c.passed) ?? [];
  return <aside className="ops-panel right glass" aria-label="Duty manager decision">
    <header className="panel-head">
      <div><span className="eyebrow">DUTY MANAGER · FINAL AUTHORITY</span><h2>Your decision, with evidence</h2></div>
      <button className="icon-btn ghost" onClick={onToggle} aria-label="Collapse decision panel"><X size={16}/></button>
    </header>
    <div className="panel-scroll">
      {run && <div className={`run-banner ${run.status}`}>
        <span className={`dot ${active ? 'pulse' : ''}`}/><strong>{STATUS[run.status] ?? run.status}</strong>
        <em>{run.mode === 'live' ? (run.provider === 'jev' ? 'Live · Jev typed decisions' : 'Live · Claude') : recorded ? 'Recorded replay · read only' : run.mode === 'replay' ? 'Illustrative replay · authored' : 'Deterministic · no AI consensus'}</em>
        <small>Episode {run.episode} · round {run.round}/2 · v{run.scenario.version}</small>
      </div>}
      {p ? <>
        <div className={`consensus ${run!.consensus ? 'yes' : 'no'}`}>{run!.consensus ? <CheckCircle2 size={16}/> : <Clock3 size={16}/>}<span>{run!.consensus ? (recorded ? 'Saved participant agreement' : 'All participants committed to this exact plan') : 'No negotiated consensus — fixed-rule result'}</span></div>
        <div className="view-switch" role="group" aria-label="Plan shown in scene">
          <button className={viewPlan.kind === 'proposal' ? 'active' : ''} onClick={() => onViewPlan({kind: 'proposal'})}>Recommended</button>
          <button className={viewPlan.kind === 'baseline' ? 'active' : ''} disabled={!run!.baseline} onClick={() => onViewPlan({kind: 'baseline'})}>Fixed rule (FCFS)</button>
        </div>
        <table className="assign">
          <thead><tr><th>Flight</th><th>Lands</th><th>Stand</th><th>Wheels-up</th></tr></thead>
          <tbody>{p.assignments.map(a => { const i = scenario.flights.findIndex(f => f.id === a.flight_id); const f = scenario.flights[i]; return <tr key={a.flight_id}>
            <td><i style={{background: FLIGHT_COLORS[i % FLIGHT_COLORS.length]}}/>{f?.callsign ?? a.flight_id}</td>
            <td>{clock(scenario.clock_start, a.arrival)}{f && a.arrival > f.reference_arrival ? <small>+{a.arrival - f.reference_arrival}</small> : null}</td>
            <td><b>{a.stand_id}</b></td><td>{clock(scenario.clock_start, a.departure)}</td></tr>; })}</tbody>
        </table>
        <div className="compare">
          <div className="compare-head"><span>SIMULATED MINUTES</span><span>FCFS</span><span>PLAN</span></div>
          {METRICS.map(([k, label]) => {
            const plan = p.metrics[k], base = run!.baseline?.metrics[k];
            const max = Math.max(1, plan ?? 0, base ?? 0);
            const delta = base !== undefined && plan !== undefined ? plan - base : null;
            return <div key={k} className="compare-row">
              <span className="lbl">{label}</span>
              <span className="val">{base ?? '—'}</span>
              <span className={`val strong ${delta !== null && delta < 0 ? 'better' : delta !== null && delta > 0 ? 'worse' : ''}`}>{plan ?? '—'}</span>
              <div className="bars"><i className="b" style={{width: `${(base ?? 0) / max * 100}%`}}/><i className="p" style={{width: `${(plan ?? 0) / max * 100}%`}}/></div>
            </div>;
          })}
          <p className="fine">{run!.baseline ? 'Identical starting conditions and constraints. Simulation outputs — not measured savings.' : 'No valid fixed-rule reference for this scenario. Missing values are not zero.'}</p>
        </div>
        <div className={`check-summary ${failed.length ? 'bad' : 'ok'}`}><ShieldCheck size={16}/>{failed.length ? `${failed.length} unresolved check${failed.length > 1 ? 's' : ''} — cannot apply` : `${p.checks.length} independent checks pass`}</div>
        {failed.slice(0, 3).map((c, i) => <CheckRow key={i} check={c}/>)}
        <div className="approve-row">
          <button className="btn primary" disabled={!canDecide || busy || !p.valid} onClick={() => onDecision('approve')}><Check size={16}/>Approve plan</button>
          <button className="btn" disabled={!canDecide || busy} onClick={() => onDecision('reject')}>Decline</button>
        </div>
        <p className="fine">{recorded ? 'Recorded replays are read-only.' : 'Approval records a simulated applied plan. No live operational clearance is issued.'}</p>
      </> : <div className="empty"><ShieldCheck size={24}/><p>A recommendation appears after participants respond. Agreement means every participant commits to one exact plan — never a majority vote.</p></div>}
      {run?.error && <p className="inline-error">{run.error}</p>}
      <button className="btn ghost full" disabled={busy || active} onClick={onFallback}><GitBranch size={14}/> Evaluate deterministic fallback</button>
      <section className="disrupt">
        <div className="sub-head"><Zap size={15}/><h3>Inject a disruption</h3><span className="tag simulated">SIMULATED</span></div>
        <p>New information invalidates commitments and approvals, archives the episode, and coordinates again. Tip: click a stand in the scene.</p>
        <label>Stand<select value={stand} onChange={e => onStand(e.target.value)} aria-label="Stand to make unavailable">{scenario.resources.stands.map(s => <option key={s.id} value={s.id}>{s.id}</option>)}</select></label>
        <button className="btn full" disabled={!run || busy || recorded || !stand || active} onClick={() => onDisrupt({type: 'stand_unavailable', stand_id: stand, available_from: 90})}><Zap size={14}/> Block stand {stand} until {clock(scenario.clock_start, 90)}</button>
        <button className="btn full" disabled={!run || busy || recorded || active} onClick={() => onDisrupt({type: 'runway_closure', start: 15, end: 40})}><Clock3 size={14}/> Close runway {clock(scenario.clock_start, 15)}–{clock(scenario.clock_start, 40)}</button>
        {scenario.approvals.filter(a => a.status !== 'confirmed').map(a => <button key={a.id} className="btn full" disabled={!run || busy || recorded || active} onClick={() => onDisrupt({type: 'approval_update', approval_id: a.id, status: 'confirmed'})}><CheckCircle2 size={14}/> Confirm {a.label}</button>)}
        {!run && <p className="fine">Start a run first — disruptions apply to an active coordination record.</p>}
      </section>
    </div>
  </aside>;
}

export function FlightCard({flight, index, plan, scenario, onClose, onEvidence}: {flight: Flight; index: number; plan: Plan | null; scenario: Scenario; onClose: () => void; onEvidence: () => void}) {
  const {t} = useSimClock(250);
  const a = plan?.assignments.find(x => x.flight_id === flight.id);
  const s = scheduleFor(flight, a);
  const st = stateAt(t, s);
  const h = flight.historical;
  const next = (flight as unknown as {next_leg?: {callsign: string; destination: {code: string}; departure_time: string}}).next_leg;
  return <div className="flight-card glass" style={{'--c': FLIGHT_COLORS[index % FLIGHT_COLORS.length]} as React.CSSProperties}>
    <header><div><span className="eyebrow">{airlineName(flight.airline)} · {flight.tail_number}</span><h3>{flight.callsign} <small>{flight.origin.code} → {flight.destination.code}</small></h3></div><button className="icon-btn ghost" onClick={onClose} aria-label="Close flight"><X size={15}/></button></header>
    <div className="phase"><i/>{s.planned || st.phase === 'enroute' ? PHASE_LABEL[st.phase] : 'Holding — awaiting coordinated plan'}</div>
    <div className="fc-grid">
      <div><span>Reference arrival</span><b>{clock(scenario.clock_start, flight.reference_arrival)}</b></div>
      <div><span>Latest safe arrival</span><b>{clock(scenario.clock_start, flight.latest_arrival)}</b></div>
      <div><span>Planned landing</span><b>{a ? clock(scenario.clock_start, a.arrival) : '—'}</b></div>
      <div><span>Stand</span><b>{a?.stand_id ?? '—'}</b></div>
      <div><span>Airborne wait</span><b>{a ? `${s.airborneWait} min` : '—'}</b></div>
      <div><span>Surface wait</span><b>{a ? `${s.surfaceWait} min` : '—'}</b></div>
    </div>
    <div className="fc-hist"><span className="tag historical">RECORDED 2015</span> sched. {histTime(h.scheduled_arrival)} · landed {histTime(h.wheels_on)} · weather delay {String(h.weather_delay ?? '—')} min · late aircraft {String(h.late_aircraft_delay ?? '—')} min</div>
    {next && <div className="fc-hist"><span className="tag simulated">CANDIDATE</span> same-tail onward leg {next.callsign} → {next.destination.code} (unconfirmed rotation)</div>}
    <button className="text-btn" onClick={onEvidence}><FileText size={12}/> Trace to source record</button>
  </div>;
}
