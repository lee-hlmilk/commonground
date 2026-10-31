// Exercise the same trajectories used by the 3D scene against real scenario plans.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, filename);
const THREE = require('three');
const {buildLayout, Flightpath} = require('../src/airport/layout.ts');
const {schedules, stateAt} = require('../src/lib/sim.ts');
const root = path.resolve(__dirname, '../..');
const python = process.env.PYTHON_EXECUTABLE || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const fixtures = JSON.parse(execFileSync(python, ['-c', `
import asyncio, copy, json, tempfile
from pathlib import Path
from backend.config import ROOT, Settings
from backend.data import DataRepository
from backend.scenarios import ScenarioRepository
from backend.solver import validate_plan, calculate_metrics
from backend.coordination import Coordinator
from backend.store import RunStore
async def main():
    repo = ScenarioRepository(DataRepository(ROOT, Settings().database_path()))
    results = []
    with tempfile.TemporaryDirectory() as directory:
        coordinator = Coordinator(RunStore(Path(directory) / 'runs.sqlite'), Settings())
        scenarios = [repo.get_scenario(item['id']) for item in repo.list_scenarios()]
        # Safety edge cases remain regression fixtures, not selectable demos.
        pending = copy.deepcopy(scenarios[0])
        pending.update(id='regression-approval')
        pending['approvals'] = [dict(id='release', label='Test release', status='pending')]
        closed = copy.deepcopy(scenarios[0])
        closed.update(id='regression-closure')
        closed['resources']['runways'][0]['closures'] = [dict(start=0, end=90)]
        for scenario in scenarios + [pending, closed]:
            run = coordinator.create(scenario, 'replay')
            await coordinator.tasks[run['id']]
            run = coordinator.store.get(run['id'])
            results.append(dict(scenario=scenario, plans=run['candidates'] + ([run['baseline']] if run.get('baseline') else []), status=run['status'], proposal=run['proposal']))
        many = copy.deepcopy(repo.get_scenario('atl-three-flight-clash'))
        many.update(id='eight-aircraft', horizon=240)
        many['flights'] = []
        many['resources']['handling_teams'] = 8
        many['resources']['stands'] = [dict(id='S' + str(i), available_from=0) for i in range(8)]
        assignments = []
        for i in range(8):
            flight = copy.deepcopy(results[0]['scenario']['flights'][i % 3])
            flight.update(id='stress-' + str(i), callsign='Stress' + str(i), reference_arrival=12 + 8*i, latest_arrival=112 + 8*i, waiting_limit=100, scheduled_departure=200, compatible_stands=['S' + str(i)])
            many['flights'].append(flight)
            assignments.append(dict(flight_id=flight['id'], arrival=20 + 8*i, in_block=28 + 8*i, off_block=110 + 10*i, departure=120 + 10*i, stand_id='S' + str(i), runway_id='RWY-1'))
        extra = dict(id='eight-aircraft-plan', scenario_version=1, assignments=assignments, metrics=calculate_metrics(many, assignments))
        extra.update(validate_plan(many, extra))
        assert extra['valid'], extra['checks']
        results.append(dict(scenario=many, plans=[extra], status='applied', proposal=None))
    print(json.dumps(results))
asyncio.run(main())
`], {cwd: root, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024}));

let checked = 0;
for (const {scenario, plans, status} of fixtures) {
  assert.equal(status, ['regression-approval', 'regression-closure'].includes(scenario.id) ? 'unresolved' : 'applied', scenario.id);
  for (const plan of [null, ...plans.filter(p => p.valid)]) {
    const scheds = schedules(scenario, plan);
    const trajectory = new Flightpath(buildLayout(scenario), scheds);
    const positions = scheds.map(() => new THREE.Vector3());
    for (let tick = 0; tick <= (scenario.horizon ?? 120) * 20; tick++) {
      const t = tick / 20;
      const poses = scheds.map((s, i) => trajectory.position(s, t, positions[i]));
      for (let i = 0; i < scheds.length; i++) for (let j = i + 1; j < scheds.length; j++) {
        if (!poses[i].visible || !poses[j].visible) continue;
        const a = positions[i], b = positions[j];
        const horizontal = Math.hypot(a.x - b.x, a.z - b.z), vertical = Math.abs(a.y - b.y);
        assert.ok(horizontal >= 6.4 || vertical >= 2,
          `${scenario.id}/${plan?.id ?? 'no-plan'} T+${t}: ${scheds[i].flightId} ${stateAt(t, scheds[i]).phase} intersects ${scheds[j].flightId} ${stateAt(t, scheds[j]).phase} (h=${horizontal}, v=${vertical})`);
      }
    }
    checked++;
  }
  console.log(`${scenario.id}: ${status}, ${plans.filter(p => p.valid).length} checked plans`);
}
const outcomes = fixtures.filter(f => f.proposal?.valid).map(f => JSON.stringify(f.proposal.metrics));
assert.equal(new Set(outcomes).size, outcomes.length, 'Recoverable scenarios should demonstrate distinct operational trade-offs');
const impossible = fixtures.find(f => f.scenario.id === 'regression-closure');
assert.equal(impossible.proposal, null, 'No safe window must never produce an arrival plan');
// A long hold must not lose its pad when a second pad becomes free and is reused.
const padScenario = fixtures[0].scenario;
const padSchedules = schedules(padScenario, null).map((s, i) => ({...s, planned: true, arrival: 20 + i * 15, inBlock: i === 0 ? 100 : 45 + (i - 1) * 20, offBlock: 120, departure: 130, surfaceWait: 1}));
const padPath = new Flightpath(buildLayout(padScenario), padSchedules);
const occupied = new THREE.Vector3(), reused = new THREE.Vector3();
padPath.position(padSchedules[0], 60, occupied);
padPath.position(padSchedules[2], 60, reused);
assert.ok(occupied.distanceTo(reused) >= 9, 'Reused holding pad must not overlap a continuing hold');
console.log(`${checked} trajectories sampled across the complete timeline without aircraft intersections.`);
