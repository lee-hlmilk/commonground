const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, filename);
const {planNames, readableMessage, mentionedPlans, decisionStory, turnSummary, decisionRuntimeSeconds, formatRuntime} = require('../src/lib/negotiation.ts');
const scenario = {clock_start: '10:15', flights: [
  {id: 'flight-1', callsign: 'DL2458', reference_arrival: 12, latest_arrival: 37, taxi_in: 8},
  {id: 'flight-2', callsign: 'WN2793', reference_arrival: 13, latest_arrival: 38, taxi_in: 8},
], resources: {stands: [{id: 'B2', available_from: 48}, {id: 'R1', available_from: 0}], runways: [{id: 'RWY-1', closures: []}]}, approvals: []};
const first = {id: 'plan-a26fe7112acb', valid: true, assignments: [], metrics: {airborne_wait: 31, surface_wait: 40, total_delay: 342}};
const selected = {id: 'plan-322bbb406816', valid: true, assignments: [
  {flight_id: 'flight-1', arrival: 28, in_block: 48, departure: 88, stand_id: 'B2'},
  {flight_id: 'flight-2', arrival: 20, in_block: 28, departure: 68, stand_id: 'R1'},
], metrics: {airborne_wait: 29, surface_wait: 26, total_delay: 326}};
const run = {candidates: [first, selected], proposal: selected, baseline: first, status: 'applied', consensus: true, round: 2, participants: [{id: 'atc'}, {id: 'ground'}], messages: []};
const names = planNames(run);
assert.equal(names.get(selected.id), 'Plan B');
assert.equal(readableMessage('Prefer plan-a26fe7112acb; plan-322bbb406816 also works.', names), 'Prefer Plan A; Plan B also works.');
assert.equal(readableMessage('Avoid a26f / 322bbb406816.', names), 'Avoid Plan A / Plan B.');
assert.equal(readableMessage('PLAN-A26FE7112ACB remains available.', names), 'Plan A remains available.');
assert.equal(readableMessage('flight-1 follows plan-deadbeef.', names, scenario), 'DL2458 follows an unlisted plan.');
assert.equal(readableMessage('The plan-based comparison is unchanged.', names), 'The plan-based comparison is unchanged.');
assert.equal(readableMessage('Compare abcd.', new Map([['plan-abcd1111', 'Plan A'], ['plan-abcd2222', 'Plan B']])), 'Compare an alternative plan.');
assert.deepEqual(mentionedPlans({statement: 'Prefer a26f over Plan B', proposal_id: selected.id}, names), [selected.id, first.id]);
const story = decisionStory(run, scenario);
assert.equal(story.title, 'Agents agreed on Plan B');
assert.match(story.explanation, /WN2793 at 10:35 and DL2458 at 10:43/);
assert.match(story.constraint, /DL2458 waits 12 minutes.*B2 opens at 11:03/);
assert.match(story.tradeoff, /2 minutes less airborne waiting, 14 minutes less ground waiting, 16 minutes less departure delay/);
assert.match(story.next, /applied automatically/);
assert.match(decisionStory({...run, status: 'fallback', consensus: false}, scenario).next, /has not been applied/);
const fallback = decisionStory({...run, status: 'fallback', consensus: false}, scenario);
assert.match(fallback.title, /fallback/);
assert.match(fallback.explanation, /without agent consensus/);
assert.doesNotMatch(fallback.title, /agreed/);
assert.match(decisionStory({...run, mode: 'recorded_replay'}, scenario).next, /read-only/);
assert.match(decisionStory({...run, status: 'rejected'}, scenario).title, /declined/);
assert.doesNotMatch(decisionStory({...run, status: 'negotiating'}, scenario).explanation, /agreed/);
const pending = {...scenario, approvals: [{id: 'release', label: 'Remote stand approval', status: 'pending'}]};
assert.match(decisionStory({...run, proposal: null, status: 'unresolved', consensus: false}, pending).explanation, /still pending/);
const closed = {...scenario, resources: {...scenario.resources, runways: [{id: 'R', closures: [{start: 0, end: 90}]}]}};
assert.match(decisionStory({...run, proposal: null, status: 'unresolved', consensus: false}, closed).explanation, /no safe landing window/);
run.messages = run.participants.map(p => ({sender: p.id, round: 2, scenario_version: 1, type: 'ACCEPT', proposal_id: selected.id}));
assert.equal(turnSummary(run, 2, 1), 'Everyone agreed on Plan B.');
run.messages[1].type = 'ESCALATE';
assert.equal(turnSummary(run, 2, 1), '1 of 2 agreed · 1 requested review.');
const beginning = '2026-10-31T00:00:00Z';
const active = {...run, status: 'negotiating', mode: 'live', metrics: {}, timing: {started_at: beginning, completed_at: null, elapsed_seconds: null}};
assert.equal(decisionRuntimeSeconds(active, Date.parse(beginning) + 12345), 12.345);
const complete = {...active, status: 'applied', timing: {...active.timing, completed_at: '2026-10-31T00:00:20Z', elapsed_seconds: 20}};
assert.equal(decisionRuntimeSeconds(complete, Date.parse(beginning) + 600000), 20, 'Completed runtime must stay fixed');
assert.equal(decisionRuntimeSeconds({...complete, mode: 'recorded_replay', created_at: '2026-11-01T00:00:00Z'}, Date.parse(beginning) + 9999999), 20, 'Recorded replay retains original duration');
assert.equal(decisionRuntimeSeconds({...run, metrics: {coordination_seconds: 9.1}}, 0), 9.1, 'Older recorded durations are supported');
assert.equal(decisionRuntimeSeconds({...run, metrics: {}}, 0), null, 'Missing duration must not become zero');
assert.equal(formatRuntime(12.34), '12.3 s');
assert.equal(formatRuntime(72.3), '1m 12.3s');
console.log('PASS: readable names, legacy IDs and abbreviations, plan references, decision wording, trade-offs, blocked outcomes and approval status.');
