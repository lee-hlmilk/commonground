import type { Assignment, Flight, Plan, Scenario } from '../types';

export const APPROACH = 5;
export const ENROUTE_VISIBLE = 30;

export type Phase = 'enroute' | 'holding' | 'approach' | 'landing' | 'taxi_in' | 'surface_hold' | 'to_stand' | 'turnaround' | 'taxi_out' | 'takeoff' | 'departed';

export const PHASE_LABEL: Record<Phase, string> = {
  enroute: 'Inbound', holding: 'Airborne holding', approach: 'Final approach', landing: 'Landing roll',
  taxi_in: 'Taxiing in', surface_hold: 'Surface holding', to_stand: 'Entering stand', turnaround: 'Turnaround',
  taxi_out: 'Taxiing out', takeoff: 'Departing', departed: 'Departed',
};

export type Schedule = {
  flightId: string; planned: boolean; arrival: number; inBlock: number; offBlock: number; departure: number;
  stand: string | null; runway: string | null; reference: number; airborneWait: number; surfaceWait: number;
  taxiIn: number;
};

export type FlightState = { phase: Phase; progress: number; schedule: Schedule };

export function scheduleFor(flight: Flight, assignment?: Assignment): Schedule {
  if (!assignment) {
    return {flightId: flight.id, planned: false, arrival: Infinity, inBlock: Infinity, offBlock: Infinity, departure: Infinity,
      stand: null, runway: null, reference: flight.reference_arrival, airborneWait: 0, surfaceWait: 0, taxiIn: flight.taxi_in};
  }
  return {flightId: flight.id, planned: true, arrival: assignment.arrival, inBlock: assignment.in_block, offBlock: assignment.off_block,
    departure: assignment.departure, stand: assignment.stand_id, runway: assignment.runway_id, reference: flight.reference_arrival,
    airborneWait: Math.max(0, assignment.arrival - flight.reference_arrival),
    surfaceWait: Math.max(0, assignment.in_block - assignment.arrival - flight.taxi_in), taxiIn: flight.taxi_in};
}

export function schedules(scenario: Scenario, plan: Plan | null | undefined) {
  return scenario.flights.map(f => scheduleFor(f, plan?.assignments.find(a => a.flight_id === f.id)));
}

const span = (t: number, a: number, b: number) => b <= a ? 1 : Math.min(1, Math.max(0, (t - a) / (b - a)));

export function stateAt(t: number, s: Schedule): FlightState {
  const fix = Math.min(s.reference, s.arrival) - APPROACH;
  if (t < fix) return {phase: 'enroute', progress: 1 - Math.min(1, (fix - t) / ENROUTE_VISIBLE), schedule: s};
  if (!s.planned || t < s.arrival - APPROACH) return {phase: 'holding', progress: t - fix, schedule: s};
  if (t < s.arrival) return {phase: 'approach', progress: span(t, s.arrival - APPROACH, s.arrival), schedule: s};
  if (t < s.arrival + 1) return {phase: 'landing', progress: span(t, s.arrival, s.arrival + 1), schedule: s};
  if (s.surfaceWait > 0) {
    const padAt = s.arrival + Math.max(2, s.taxiIn - 1);
    if (t < padAt) return {phase: 'taxi_in', progress: span(t, s.arrival + 1, padAt), schedule: s};
    if (t < s.inBlock - 1) return {phase: 'surface_hold', progress: span(t, padAt, s.inBlock - 1), schedule: s};
    if (t < s.inBlock) return {phase: 'to_stand', progress: span(t, s.inBlock - 1, s.inBlock), schedule: s};
  } else if (t < s.inBlock) return {phase: 'taxi_in', progress: span(t, s.arrival + 1, s.inBlock), schedule: s};
  if (t < s.offBlock) return {phase: 'turnaround', progress: span(t, s.inBlock, s.offBlock), schedule: s};
  if (t < s.departure) return {phase: 'taxi_out', progress: span(t, s.offBlock, s.departure), schedule: s};
  if (t < s.departure + 2) return {phase: 'takeoff', progress: span(t, s.departure, s.departure + 2), schedule: s};
  return {phase: 'departed', progress: 1, schedule: s};
}

export function horizonOf(scenario: Scenario, plans: (Plan | null | undefined)[]) {
  const base = Number((scenario as unknown as {horizon?: number}).horizon ?? 120);
  const ends = plans.flatMap(p => p?.assignments.map(a => a.departure + 6) ?? []);
  return Math.max(base, ...ends);
}

export function standStatus(scenario: Scenario, scheds: Schedule[], standId: string, t: number) {
  const stand = scenario.resources.stands.find(s => s.id === standId);
  const occupant = scheds.find(s => s.stand === standId && t >= s.inBlock && t < s.offBlock);
  if (occupant) return {state: 'occupied' as const, flightId: occupant.flightId};
  if (stand && t < stand.available_from) return {state: 'unavailable' as const, until: stand.available_from};
  const next = scheds.filter(s => s.stand === standId && s.inBlock > t).sort((a, b) => a.inBlock - b.inBlock)[0];
  return next ? {state: 'reserved' as const, flightId: next.flightId, at: next.inBlock} : {state: 'free' as const};
}

export function runwayClosed(scenario: Scenario, runwayId: string, t: number) {
  return scenario.resources.runways.find(r => r.id === runwayId)?.closures.some(c => t >= c.start && t < c.end) ?? false;
}
