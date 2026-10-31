import type { Assignment, Plan, Run, Scenario } from '../types';
import { clock } from './format';

export type CriticalChoice = {kind: string; title: string; detail: string; comparisonPlanId?: string};
export type FlightConsequence = {flight: string; airborne: number; ground: number; arrivalMargin: number; change?: string};

export function analyzeDecision(run: Run, scenario: Scenario, names: Map<string, string>) {
  const plan = run.proposal!;
  const label = (p: Plan) => names.get(p.id) ?? 'the compared plan';
  const flight = (id: string) => scenario.flights.find(f => f.id === id)!;
  const call = (id: string) => flight(id)?.callsign ?? 'Aircraft';
  const at = (minute: number) => clock(scenario.clock_start, minute);
  const ground = (a: Assignment) => a.in_block - a.arrival - flight(a.flight_id).taxi_in;
  const margin = (a: Assignment) => {
    const f = flight(a.flight_id);
    return Math.min(f.latest_arrival, f.reference_arrival + (f.waiting_limit ?? Infinity)) - a.arrival;
  };
  const choices: CriticalChoice[] = [];
  const rows = [...plan.assignments].sort((a, b) => a.arrival - b.arrival);

  const forced = rows.find(a => flight(a.flight_id).compatible_stands?.length === 1);
  if (forced) {
    const stand = scenario.resources.stands.find(s => s.id === forced.stand_id)!;
    choices.push({kind: 'stand', title: `${call(forced.flight_id)} must use ${stand.id}`,
      detail: `Only ${stand.id} is compatible in this scenario, so switching to another stand is not an available workaround. ${stand.available_from > 0 ? `${stand.id} opens at ${at(stand.available_from)}. ` : ''}The plan lands this aircraft at ${at(forced.arrival)}, allows ${ground(forced)} minutes of ground holding, and enters the stand at ${at(forced.in_block)}.`});
  }
  const ramp = (scenario.assumptions ?? []).find(a => a.id === 'ramp-suspension');
  if (ramp && typeof ramp.value === 'number' && rows.length) {
    choices.push({kind: 'ramp', title: 'Landing does not mean the ramp is ready',
      detail: `Ramp work is suspended until ${at(ramp.value)}. ${call(rows[0].flight_id)} lands first at ${at(rows[0].arrival)} but enters ${rows[0].stand_id} at ${at(rows[0].in_block)}, with ${ground(rows[0])} minutes of ground holding. The plan separates runway recovery from permission to start stand handling.`});
  }
  for (const runway of scenario.resources.runways) {
    const affected = scenario.flights.filter(f => plan.assignments.some(a => a.flight_id === f.id && a.runway_id === runway.id));
    const closure = runway.closures.find(c => affected.some(f => f.reference_arrival < c.end && f.reference_arrival + runway.separation > c.start));
    const first = rows.find(a => a.runway_id === runway.id);
    if (closure && first) choices.push({kind: 'runway', title: `Work around ${runway.id}'s closure`,
      detail: `${runway.id} is closed ${at(closure.start)}–${at(closure.end)}. The selected sequence starts with ${call(first.flight_id)} at ${at(first.arrival)}${first.arrival === closure.end ? ', exactly when it reopens' : ''}. Every landing and departure must also keep ${runway.separation} minutes of runway separation.`});
  }

  let explainedHold = false;
  for (const a of [...rows].sort((a, b) => ground(b) - ground(a))) {
    const ready = a.arrival + flight(a.flight_id).taxi_in;
    const stand = scenario.resources.stands.find(s => s.id === a.stand_id)!;
    const busy = rows.filter(b => b.flight_id !== a.flight_id && b.in_block <= ready && b.off_block > ready);
    if (ground(a) > 0 && stand.available_from <= ready && scenario.resources.handling_teams > 0 && busy.length >= scenario.resources.handling_teams && Math.min(...busy.map(b => b.off_block)) === a.in_block) {
      const released = busy.find(b => b.off_block === a.in_block)!;
      choices.push({kind: 'handling', title: `Hold ${call(a.flight_id)} for a handling team`,
        detail: `${call(a.flight_id)} finishes its taxi allowance at ${at(ready)}, while all ${scenario.resources.handling_teams} handling teams are serving ${busy.map(b => call(b.flight_id)).join(' and ')}. ${call(released.flight_id)} releases a team at ${at(a.in_block)}. In this handling sequence, ${call(a.flight_id)} waits ${ground(a)} minutes before entering ${a.stand_id}; an open stand alone is insufficient.`});
      explainedHold = true;
      break;
    }
  }
  if (!explainedHold && scenario.resources.surface_movement_capacity === 1 && rows.length > 1) {
    const pair = rows.slice(1).map((a, i) => [rows[i], a]).find(([a, b]) => a.arrival + flight(a.flight_id).taxi_in === b.arrival);
    if (pair) choices.push({kind: 'corridor', title: 'Keep aircraft out of each other’s taxi route',
      detail: `The shared surface route allows one moving aircraft at a time. ${call(pair[0].flight_id)} reserves it ${at(pair[0].arrival)}–${at(pair[1].arrival)}; ${call(pair[1].flight_id)} starts next. This limits the sequence even when the runway's minimum separation would permit closer arrivals.`});
  }
  const closest = [...rows].sort((a, b) => margin(a) - margin(b))[0];
  if (closest) choices.push({kind: 'margin', title: `Closest arrival limit: ${call(closest.flight_id)}`,
    detail: `${call(closest.flight_id)} lands at ${at(closest.arrival)}, with ${margin(closest)} minutes left before its permitted arrival limit at ${at(closest.arrival + margin(closest))}. Its added airborne wait is ${closest.arrival - flight(closest.flight_id).reference_arrival} minutes. This margin uses the scenario’s declared waiting limit.`});

  const firstTurn = [...new Map(run.messages.filter(m => m.round === 1 && m.scenario_version === scenario.version).map(m => [m.sender, m])).values()];
  const eligible = run.candidates.filter(p => p.valid && p.id !== plan.id && p.scenario_version === plan.scenario_version);
  const votes = (p: Plan) => firstTurn.filter(m => m.preferred_plan_id === p.id).length;
  const alternative = [...eligible].sort((a, b) => votes(b) - votes(a) || Math.abs(b.metrics.airborne_wait - plan.metrics.airborne_wait) - Math.abs(a.metrics.airborne_wait - plan.metrics.airborne_wait))[0];
  const finalAccepts = new Set(run.messages.filter(m => m.round === run.round && m.type === 'ACCEPT' && m.proposal_id === plan.id && m.scenario_version === scenario.version).map(m => m.sender));
  if (run.consensus && finalAccepts.size === run.participants.length && firstTurn.length === run.participants.length && firstTurn.every(m => Array.isArray(m.acceptable_plan_ids))) {
    const stats = (p: Plan) => {
      const ranks = firstTurn.map(m => {
        const order = [...(m.acceptable_plan_ids ?? [])];
        if (m.preferred_plan_id && order.includes(m.preferred_plan_id)) {order.splice(order.indexOf(m.preferred_plan_id), 1); order.unshift(m.preferred_plan_id);}
        return order.includes(p.id) ? order.indexOf(p.id) + 1 : Infinity;
      });
      return {support: ranks.filter(Number.isFinite).length, worst: Math.max(...ranks)};
    };
    const selected = stats(plan), other = alternative && stats(alternative);
    const balance = other && selected.support === run.participants.length && other.support === selected.support && selected.worst < other.worst
      ? `Compared with ${label(alternative!)}, its least-satisfied participant ranks it ${selected.worst} of ${run.candidates.length} rather than ${other.worst}. That matches the coordinator’s preference-balancing priority.` : '';
    choices.push({kind: 'selection', title: `Why the participants settled on ${label(plan)}`,
      detail: `${run.mode === 'replay' || firstTurn.some(m => m.fixture) ? 'In this illustrative replay, ' : 'On turn one, '}${selected.support} of ${run.participants.length} participants listed ${label(plan)} as acceptable and ${votes(plan)} preferred it first. ${balance} All ${run.participants.length} later accepted the exact selected plan on turn ${run.round}.`});
  }

  const consequences: FlightConsequence[] = rows.map(a => {
    const b = run.baseline?.assignments.find(b => b.flight_id === a.flight_id);
    const changes: string[] = [];
    if (b) {
      if (a.arrival !== b.arrival) changes.push(`lands ${Math.abs(a.arrival - b.arrival)} min ${a.arrival < b.arrival ? 'earlier' : 'later'}`);
      if (ground(a) !== ground(b)) changes.push(`${Math.abs(ground(a) - ground(b))} min ${ground(a) < ground(b) ? 'less' : 'more'} ground waiting`);
      if (a.departure !== b.departure) changes.push(`departs ${Math.abs(a.departure - b.departure)} min ${a.departure < b.departure ? 'earlier' : 'later'}`);
      if (a.stand_id !== b.stand_id) changes.push(`uses ${a.stand_id} instead of ${b.stand_id}`);
    }
    return {flight: call(a.flight_id), airborne: a.arrival - flight(a.flight_id).reference_arrival, ground: ground(a), arrivalMargin: margin(a), change: changes.join('; ') || undefined};
  });
  let alternativeChoice: CriticalChoice | undefined;
  if (alternative) {
    const differences = ([['airborne_wait', 'airborne waiting'], ['surface_wait', 'ground waiting'], ['total_delay', 'departure delay']] as const).flatMap(([key, title]) => {
      const delta = alternative.metrics[key] - plan.metrics[key];
      return Number.isFinite(delta) && delta !== 0 ? [`${Math.abs(delta)} minute${Math.abs(delta) === 1 ? '' : 's'} ${delta < 0 ? 'less' : 'more'} ${title}`] : [];
    });
    const specific = rows.flatMap(a => {
      const other = alternative.assignments.find(b => b.flight_id === a.flight_id);
      if (!other) return [];
      if (other.arrival !== a.arrival) return [`${call(a.flight_id)} would land at ${at(other.arrival)} instead of ${at(a.arrival)}`];
      if (other.stand_id !== a.stand_id) return [`${call(a.flight_id)} would use ${other.stand_id} instead of ${a.stand_id}`];
      return [];
    }).slice(0, 2);
    alternativeChoice = {kind: 'alternative', title: `What ${label(alternative)} would change`, comparisonPlanId: alternative.id,
      detail: [differences.length ? `${label(alternative)} would mean ${differences.join(', ')} across all flights.` : `${label(alternative)} has the same aggregate waiting and departure-delay totals; the assignments can still differ.`, specific.length ? specific.join('. ') + '.' : '', votes(alternative) ? `${votes(alternative)} participant${votes(alternative) === 1 ? '' : 's'} initially preferred it.` : '', run.consensus ? 'This option was not the final agreement.' : 'This is another checked preview, without agent consensus.'].filter(Boolean).join(' ')};
  }
  return {choices, consequences, alternative: alternativeChoice};
}
