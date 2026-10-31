import type { Message, Plan, Run, Scenario } from '../types';
import { clock } from './format';
import { analyzeDecision, type CriticalChoice, type FlightConsequence } from './decisionAnalysis';

export function decisionRuntimeSeconds(run: Run, currentTime: number): number | null {
  const finished = run.timing?.elapsed_seconds ?? run.metrics.coordination_seconds;
  if (typeof finished === 'number' && Number.isFinite(finished) && finished >= 0) return finished;
  if (run.status === 'negotiating' && run.mode !== 'recorded_replay' && run.timing?.started_at) {
    const start = Date.parse(run.timing.started_at);
    if (Number.isFinite(start)) return Math.max(0, (currentTime - start) / 1000);
  }
  return null;
}

export function formatRuntime(seconds: number): string {
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(1)}s`;
}

export function planNames(run: Run | null): Map<string, string> {
  const names = new Map<string, string>();
  run?.candidates.forEach((p, i) => names.set(p.id, p.label ?? `Plan ${String.fromCharCode(65 + i)}`));
  if (run?.baseline && !names.has(run.baseline.id)) names.set(run.baseline.id, run.baseline.label ?? 'Baseline plan');
  if (run?.proposal && !names.has(run.proposal.id)) names.set(run.proposal.id, run.proposal.label ?? (run.status === 'fallback' ? 'Fallback plan' : 'Selected plan'));
  return names;
}

export function readableMessage(statement: string, names: Map<string, string>, scenario?: Scenario): string {
  let text = statement.replace(/\b(?:plan-)[a-z0-9-]+\b/gi, id => names.get(id.toLowerCase()) ?? (/^plan-[a-f0-9]{4,64}$/i.test(id) ? 'an unlisted plan' : id));
  // Earlier model messages sometimes abbreviated IDs to an unambiguous hash prefix.
  text = text.replace(/\b[a-f0-9]{4,16}\b/gi, token => {
    if (!/[a-f]/i.test(token)) return token;
    const matches = [...names].filter(([id]) => id.replace(/^plan-/, '').startsWith(token.toLowerCase()));
    return matches.length === 1 ? matches[0][1] : matches.length > 1 ? 'an alternative plan' : token;
  });
  for (const flight of scenario?.flights ?? []) text = text.replaceAll(flight.id, flight.callsign);
  return text;
}

export function mentionedPlans(message: Message, names: Map<string, string>): string[] {
  const ids = new Set<string>();
  for (const id of [message.proposal_id, message.preferred_plan_id]) if (id && names.has(id)) ids.add(id);
  for (const [id, label] of names) {
    if (message.statement.includes(id) || message.statement.includes(label)) ids.add(id);
    const hash = id.replace(/^plan-/, '');
    for (const token of message.statement.match(/\b[a-f0-9]{4,16}\b/gi) ?? []) {
      if (/[a-f]/i.test(token) && hash.startsWith(token.toLowerCase()) && [...names.keys()].filter(key => key.replace(/^plan-/, '').startsWith(token.toLowerCase())).length === 1) ids.add(id);
    }
  }
  return [...ids];
}

export const MESSAGE_ACTION: Record<string, string> = {
  PROPOSE: 'Proposes', ACCEPT: 'Agrees', UNABLE: 'Cannot accept', ESCALATE: 'Needs review',
  REQUEST: 'Asks for clarification', UPDATE: 'Shares an update', PREFER: 'Prefers', REJECT: 'Declines', COUNTER: 'Suggests a change',
};

export function turnSummary(run: Run, turn: number, version: number): string {
  const messages = run.messages.filter(m => m.round === turn && m.scenario_version === version);
  if (turn === 1) return `${messages.length} of ${run.participants.length} participants have shared their needs and preferred options.`;
  const agreed = messages.filter(m => m.type === 'ACCEPT').length;
  const refused = messages.filter(m => m.type === 'UNABLE').length;
  const review = messages.filter(m => m.type === 'ESCALATE' || m.type === 'REQUEST').length;
  const proposals = new Set(messages.map(m => m.proposal_id));
  if (agreed === run.participants.length && proposals.size === 1) return `Everyone agreed on ${planNames(run).get(messages[0].proposal_id!) ?? 'the shared plan'}.`;
  return [`${agreed} of ${run.participants.length} agreed`, refused ? `${refused} could not accept` : '', review ? `${review} requested review` : ''].filter(Boolean).join(' · ') + '.';
}

const join = (items: string[]) => items.length < 3 ? items.join(' and ') : `${items.slice(0, -1).join(', ')}, then ${items.at(-1)}`;

export function landingSequence(plan: Plan, scenario: Scenario): string {
  return join([...plan.assignments].sort((a, b) => a.arrival - b.arrival).map(a => `${scenario.flights.find(f => f.id === a.flight_id)?.callsign ?? 'an aircraft'} at ${clock(scenario.clock_start, a.arrival)}`));
}

export type DecisionStory = {title: string; explanation: string; constraint?: string; tradeoff?: string; choices?:CriticalChoice[]; consequences?:FlightConsequence[]; alternative?:CriticalChoice; next: string; tone: 'agreed' | 'review' | 'waiting'};

export function decisionStory(run: Run, scenario: Scenario): DecisionStory {
  const names = planNames(run), p = run.proposal;
  const name = p ? names.get(p.id) ?? 'the selected plan' : 'a recovery plan';
  const pending = scenario.approvals.filter(a => a.status !== 'confirmed');
  if (run.status === 'negotiating') return {title: p ? `Discussing ${name}` : 'Finding a shared plan', explanation: 'Air traffic control, each captain and ground operations are comparing options. No final decision has been made.', next: 'Wait for each participant to respond.', tone: 'waiting'};
  if (run.status === 'interrupted') return {title: 'Coordination was interrupted', explanation: 'This run did not finish. A partial proposal does not count as an agreement.', next: 'Start a new coordination run.', tone: 'review'};
  if (!p || !p.valid) {
    const closure = scenario.resources.runways.length === 1 ? scenario.resources.runways[0].closures.find(c => scenario.flights.every(f => c.start <= f.reference_arrival && c.end > f.latest_arrival)) : undefined;
    return {title: pending.length ? 'External approval is still needed' : 'No checked plan is ready',
      explanation: pending.length ? `${pending.map(a => a.label).join('; ')} ${pending.length === 1 ? 'is' : 'are'} still ${pending.map(a => a.status).join(' / ')}. The agents cannot authorise this release themselves.` : closure ? `The runway stays closed until ${clock(scenario.clock_start, closure.end)}, beyond every aircraft’s latest permitted arrival. There is no safe landing window under these conditions.` : readableMessage(run.error ?? 'The participants could not produce a plan that passes all operational checks.', names, scenario),
      next: 'The blocking condition must be resolved before coordination can complete.', tone: 'review'};
  }
  const agreed = run.consensus && run.status !== 'fallback';
  const applied = run.status === 'applied' || run.status === 'approved';
  const title = run.status === 'rejected' ? `${name} was declined` : agreed ? `Agents agreed on ${name}` : applied ? `${name} was applied` : `A fallback is available: ${name}`;
  const explanation = `${agreed ? `All ${run.participants.length} participants agreed after ${run.round} ${run.round === 1 ? 'turn' : 'turns'}.` : 'This is a first-come-first-served recovery option, without agent consensus.'} Land ${landingSequence(p, scenario)}.`;
  const constraints = [...p.assignments].sort((a, b) => (scenario.resources.stands.find(s => s.id === b.stand_id)?.available_from ?? 0) - (scenario.resources.stands.find(s => s.id === a.stand_id)?.available_from ?? 0)).map(a => {
    const flight = scenario.flights.find(f => f.id === a.flight_id)!;
    const stand = scenario.resources.stands.find(s => s.id === a.stand_id)!;
    const wait = a.in_block - a.arrival - flight.taxi_in;
    if (wait <= 0) return '';
    return stand.available_from > 0 && a.in_block === stand.available_from
      ? `${flight.callsign} waits ${wait} minutes in ground holding until stand ${stand.id} opens at ${clock(scenario.clock_start, a.in_block)}.`
      : `${flight.callsign} waits ${wait} minutes in ground holding, then parks at ${stand.id} at ${clock(scenario.clock_start, a.in_block)}.`;
  }).filter(Boolean);
  const base = run.baseline;
  const analysis = analyzeDecision(run, scenario, names);
  const changes = base ? ([['airborne_wait', 'airborne waiting'], ['surface_wait', 'ground waiting'], ['total_delay', 'departure delay']] as const).flatMap(([key, label]) => {
    const actual = p.metrics[key], reference = base.metrics[key];
    if (actual === undefined || reference === undefined || actual === reference) return [];
    return [`${Math.abs(actual - reference)} minutes ${actual < reference ? 'less' : 'more'} ${label}`];
  }) : [];
  const sameAssignments = base && planSameAs(p, base);
  const tradeoff = base ? changes.length ? `Compared with first-come-first-served: ${changes.join(', ')} across all flights.` : sameAssignments ? 'The selected schedule is identical to the checked first-come-first-served baseline. There is no simulated waiting or departure-delay improvement over that baseline.' : 'Aggregate waiting and departure-delay totals tie with first-come-first-served. The flight-level changes below show the different arrival, holding or stand choices hidden by those totals.' : `No valid first-come-first-served comparison was retained. This plan has ${p.metrics.airborne_wait ?? '—'} minutes added airborne waiting and ${p.metrics.surface_wait ?? '—'} minutes ground waiting across all flights.`;
  return {title, explanation, constraint: constraints.join(' ') || 'The assigned stands, handling teams and shared taxi route pass the operational checks.', tradeoff,
    choices: analysis.choices, consequences: analysis.consequences, alternative: analysis.alternative,
    next: run.mode === 'recorded_replay' ? `This saved decision is read-only.${applied ? ' It was applied in the original simulation; replay does not apply it again.' : ' No new plan is applied by replaying it.'}` : applied ? 'The checked agreement was applied automatically inside this simulation.' : run.status === 'rejected' ? 'This historical plan was declined and was not applied.' : agreed ? 'This older agreement was not automatically applied. Start a new run to use automatic application.' : 'Without agent consensus, this fallback remains a preview and has not been applied.', tone: agreed ? 'agreed' : 'review'};
}

function planSameAs(a: Plan, b: Plan): boolean {
  return a.assignments.length === b.assignments.length && a.assignments.every(row => {
    const other = b.assignments.find(x => x.flight_id === row.flight_id);
    return other && (['arrival', 'in_block', 'off_block', 'departure', 'stand_id', 'runway_id'] as const).every(key => row[key] === other[key]);
  });
}
