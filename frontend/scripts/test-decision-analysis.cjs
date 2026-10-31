const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText, filename);
const {decisionStory} = require('../src/lib/negotiation.ts');
const root = path.resolve(__dirname, '../..');
const python = process.env.PYTHON_EXECUTABLE || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const runs = JSON.parse(execFileSync(python, ['-c', `
import asyncio,json,tempfile
from pathlib import Path
from backend.config import ROOT,Settings
from backend.data import DataRepository
from backend.scenarios import ScenarioRepository
from backend.coordination import Coordinator
from backend.store import RunStore
async def main():
    repo=ScenarioRepository(DataRepository(ROOT,Settings().database_path()))
    results=[]
    with tempfile.TemporaryDirectory() as folder:
        c=Coordinator(RunStore(Path(folder)/'runs.sqlite'),Settings())
        for item in repo.list_scenarios():
            r=c.create(repo.get_scenario(item['id']),'replay')
            await c.tasks[r['id']]
            results.append(c.store.get(r['id']))
    print(json.dumps(results))
asyncio.run(main())
`], {cwd: root, encoding: 'utf8', maxBuffer: 10 * 1024 * 1024}));
const stories = runs.map(r => decisionStory(r, r.scenario));
assert.equal(new Set(stories.map(s => JSON.stringify(s.choices))).size, 4, 'Each preset needs an explanation of its own critical constraints');
for (let i = 0; i < runs.length; i++) {
  const r = runs[i], s = stories[i];
  assert.ok(s.choices.some(c => c.kind === 'selection'));
  assert.ok(s.choices.some(c => c.kind === 'margin'));
  assert.equal(s.consequences.length, r.scenario.flights.length);
  assert.ok(s.alternative && r.candidates.some(p => p.valid && p.id === s.alternative.comparisonPlanId));
  assert.ok(!/plan-[a-f0-9]+/.test(JSON.stringify(s.choices.map(c => c.detail))));
  for (const row of s.consequences) assert.ok(row.arrivalMargin >= 0);
  console.log(`${r.scenario.id}: ${r.proposal.label}; ${s.choices.map(c => c.kind).join(', ')}`);
}
const equipment = stories[runs.findIndex(r => r.scenario.id === 'equipment-failure')];
assert.match(equipment.choices.find(c => c.kind === 'stand').detail, /Only B2.*11:03/);
const weather = stories[runs.findIndex(r => r.scenario.id === 'ramp-weather')];
assert.match(weather.choices.find(c => c.kind === 'ramp').detail, /suspended until 10:50/);
const runway = stories[runs.findIndex(r => r.scenario.id === 'runway-restriction')];
assert.match(runway.choices.find(c => c.kind === 'runway').detail, /10:15–10:42/);
const base = runs[0];
const identical = decisionStory({...base, baseline: structuredClone(base.proposal)}, base.scenario);
assert.match(identical.tradeoff, /identical.*no simulated/i);
// Equal aggregate scores must still reveal a different concrete allocation.
const tied = base.candidates.find(p => p.id !== base.proposal.id && JSON.stringify(p.metrics) === JSON.stringify(base.proposal.metrics));
assert.ok(tied, 'Regression fixture includes a distinct option with matching totals');
const equalTotals = decisionStory({...base, baseline: tied}, base.scenario);
assert.match(equalTotals.tradeoff, /totals tie/);
assert.ok(equalTotals.consequences.some(c => c.change), 'Different assignments must be exposed even when totals tie');
const missingPreferences = decisionStory({...base, messages: []}, base.scenario);
assert.ok(!missingPreferences.choices.some(c => c.kind === 'selection'), 'Do not invent agent reasoning when preference records are absent');
const fallback = decisionStory({...base, status: 'fallback', consensus: false}, base.scenario);
assert.ok(!fallback.choices.some(c => c.kind === 'selection'));
assert.match(fallback.explanation, /without agent consensus/);
console.log('PASS: all four real presets, binding constraints, preference evidence, per-aircraft consequences, checked alternatives, metric ties and missing-evidence handling.');
