import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.agents import context_for, illustrative_reply, participants, validate_reply
from backend.config import Settings
from backend.coordination import Coordinator
from backend.main import create_app
from backend.solver import generate_candidates, validate_plan
from backend.store import RunStore
from tests.test_solver import scenario


def operation():
    s = scenario()
    s.update(name='Test recovery', description='Test', date='2015-12-30', clock_start='10:15',
             airport={'code': 'ATL', 'name': 'Atlanta'}, assumptions=[], provenance={})
    for f in s['flights']:
        f.update(origin={'code': 'ABC', 'name': 'Origin'}, destination={'code': 'ATL', 'name': 'Atlanta'},
                 historical={}, source={}, airline={'code': 'AA', 'name': 'Test Airline'})
    return s


class FakeClient:
    calls = []
    def __init__(self, settings):
        pass
    async def respond(self, context):
        self.calls.append(context)
        return validate_reply(illustrative_reply(context['participant'], context), context)
    async def close(self):
        pass


class RejectClient(FakeClient):
    async def respond(self, context):
        reply = await super().respond(context)
        if context['round'] == 2:
            reply['type'] = 'UNABLE'
        return reply


class CoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.tmp.name) / 'test.sqlite')
        self.settings = Settings(provider='claude', api_key='test-key', deadline=10)
        FakeClient.calls = []
        self.coordinator = Coordinator(self.store, self.settings, FakeClient)

    async def asyncTearDown(self):
        for task in self.coordinator.tasks.values():
            task.cancel()
        await asyncio.gather(*self.coordinator.tasks.values(), return_exceptions=True)
        self.tmp.cleanup()

    async def finish(self, run):
        await self.coordinator.tasks[run['id']]
        return self.store.get(run['id'])

    async def test_independent_roles_two_rounds_and_metrics_remain_valid(self):
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['status'], 'awaiting_approval')
        self.assertTrue(run['consensus'])
        self.assertEqual(len(FakeClient.calls), len(run['participants']) * 2)
        self.assertTrue(validate_plan(run['scenario'], run['proposal'])['valid'])
        self.assertNotIn('coordination_seconds', run['proposal']['metrics'])

    async def test_rejection_falls_back_without_inventing_consensus(self):
        self.coordinator.client_factory = RejectClient
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['status'], 'fallback')
        self.assertFalse(run['consensus'])

    async def test_pending_external_approval_never_agrees(self):
        s = operation()
        s['approvals'] = [dict(id='release', label='External release', status='pending')]
        run = await self.finish(self.coordinator.create(s, 'replay'))
        self.assertEqual(run['status'], 'unresolved')
        self.assertFalse(run['consensus'])
        self.assertTrue(any(not c['passed'] for c in run['checks']))

    async def test_disruption_invalidates_prior_commitments(self):
        run = await self.finish(self.coordinator.create(operation(), 'replay'))
        run['applied_plan'] = copy.deepcopy(run['proposal'])
        run['decision'] = {'decision': 'approve'}
        updated = self.coordinator.disrupt(run, dict(type='stand_unavailable', stand_id='S1', available_from=90))
        self.assertEqual(updated['scenario']['version'], 2)
        self.assertEqual(updated['episode'], 2)
        self.assertIsNone(updated['proposal'])
        self.assertFalse(updated['consensus'])
        self.assertNotIn('applied_plan', updated)
        self.assertNotIn('decision', updated)
        self.assertNotIn('baseline', updated)
        await self.finish(updated)
        self.assertTrue(updated['episodes'][0]['messages'])
        self.assertIn('applied_plan', updated['episodes'][0])

    def test_private_aircraft_context_and_unknown_evidence(self):
        s = operation()
        plans = generate_candidates(s, 2, 1)
        pilot = participants(s)[1]
        context = context_for(pilot, s, plans, [], None, 1)
        self.assertEqual(context['private_information']['aircraft']['id'], 'A')
        self.assertNotIn('flight:B:waiting_limit', context['evidence'])
        reply = illustrative_reply(pilot, context)
        reply['evidence_refs'] = ['invented:approval']
        with self.assertRaises(ValueError):
            validate_reply(reply, context)

    def test_event_cursors_and_restart_interruption(self):
        self.store.save_scenario(operation())
        run = dict(id='test', status='negotiating', scenario={}, episode=1)
        first = self.store.save(run)
        second = self.store.save(run)
        self.assertEqual([i for i, _ in self.store.events('test', first)], [second])
        restarted = RunStore(self.store.path)
        self.assertEqual(restarted.get('test')['status'], 'interrupted')
        self.assertEqual(restarted.scenarios()[0]['id'], 'test')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(provider='claude', api_key='', database=str(Path(self.tmp.name) / 'api.sqlite'))
        self.repo = type('Repo', (), {'summary': lambda self: {'flight_rows': 3}})()
        self.app = create_app(self.settings, self.repo)
        self.patch = patch('backend.scenarios.ScenarioRepository.get_scenario', return_value=operation())
        self.patch.start()
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.patch.stop()
        self.tmp.cleanup()

    def test_missing_key_is_explicit_and_not_exposed(self):
        health = self.client.get('/api/health').json()
        self.assertFalse(health['claude_configured'])
        self.assertNotIn('api_key', health)
        self.assertEqual(self.client.post('/api/runs', json={'mode': 'live'}).status_code, 400)

    def test_approval_revalidates_current_version_and_changes_simulated_state(self):
        s = operation()
        p = generate_candidates(s, 1, 1)[0]
        run = dict(id='approve', status='awaiting_approval', mode='live', scenario=s, episode=1,
                   proposal=p, consensus=True)
        self.app.state.store.save(run)
        body = dict(proposal_id=p['id'], scenario_version=0, decision='approve')
        self.assertEqual(self.client.post('/api/runs/approve/decision', json=body).status_code, 409)
        body['scenario_version'] = 1
        approved = self.client.post('/api/runs/approve/decision', json=body)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()['applied_plan']['id'], p['id'])

    def test_pending_approval_cannot_be_bypassed_by_manager(self):
        s = operation()
        p = generate_candidates(s, 1, 1)[0]
        s['approvals'] = [dict(id='release', status='pending')]
        self.app.state.store.save(dict(id='blocked', status='fallback', mode='live', scenario=s,
                                      proposal=p, episode=1, consensus=False))
        body = dict(proposal_id=p['id'], scenario_version=1, decision='approve')
        self.assertEqual(self.client.post('/api/runs/blocked/decision', json=body).status_code, 409)

    def test_incomplete_disruption_is_validation_error(self):
        r = self.client.post('/api/runs/unknown/disruptions', json={'type': 'runway_closure'})
        self.assertEqual(r.status_code, 422)


if __name__ == '__main__':
    unittest.main()
