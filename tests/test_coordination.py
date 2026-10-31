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
        if context['round'] >= 2:
            reply['type'] = 'UNABLE'
        return reply


class ReviewClient(FakeClient):
    async def respond(self, context):
        reply = await super().respond(context)
        if context['round'] == 2:
            reply.update(type='ESCALATE', acceptable_plan_ids=[], preferred_plan_id=None)
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
        self.assertEqual(run['status'], 'applied')
        self.assertTrue(run['consensus'])
        self.assertEqual(len(FakeClient.calls), len(run['participants']) * 2)
        self.assertTrue(validate_plan(run['scenario'], run['proposal'])['valid'])
        self.assertEqual([p['label'] for p in run['candidates']], [f'Plan {chr(65+i)}' for i in range(len(run['candidates']))])
        self.assertEqual(FakeClient.calls[0]['candidates'][0]['label'], 'Plan A')
        self.assertNotIn('coordination_seconds', run['proposal']['metrics'])
        self.assertIsNotNone(run['timing']['completed_at'])
        self.assertGreaterEqual(run['timing']['elapsed_seconds'], 0)
        self.assertEqual(run['metrics']['coordination_seconds'], run['timing']['elapsed_seconds'])

    async def test_rejection_falls_back_without_inventing_consensus(self):
        self.coordinator.client_factory = RejectClient
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['status'], 'fallback')
        self.assertFalse(run['consensus'])
        self.assertNotIn('applied_plan', run)
        self.assertLessEqual(run['round'], 3)
        self.assertIsNotNone(run['timing']['completed_at'])

    async def test_third_turn_resolves_prior_escalations_with_explicit_evidence(self):
        self.coordinator.client_factory = ReviewClient
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['round'], 3)
        self.assertTrue(run['consensus'])
        self.assertEqual(run['status'], 'applied')
        self.assertEqual(len(run['messages']), len(run['participants']) * 3)
        self.assertTrue(all(m['type'] == 'ESCALATE' for m in run['messages'] if m['round'] == 2))
        contexts = [c for c in FakeClient.calls if c['round'] == 3]
        self.assertTrue(all(c['reconciliation']['final_turn'] for c in contexts))
        self.assertTrue(all(c['proposal_assessment']['all_checks_pass'] for c in contexts))
        self.assertTrue(all(c['proposal_assessment']['required_external_approvals'] == [] for c in contexts))
        pilot = next(c for c in contexts if c['participant']['role'] == 'pilot')
        self.assertIn('own_aircraft_outcome', pilot['proposal'])
        self.assertEqual(run['applied_plan']['id'], run['proposal']['id'])

    async def test_unresolved_refusal_stops_at_third_turn(self):
        self.coordinator.client_factory = RejectClient
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['round'], 3)
        self.assertFalse(run['consensus'])
        self.assertEqual(run['status'], 'fallback')
        self.assertTrue(all(m['round'] <= 3 for m in run['messages']))
        self.assertNotEqual(run['proposal_history'][0]['proposal']['id'], run['proposal_history'][0]['next_proposal_id'])

    async def test_pending_external_approval_never_agrees(self):
        s = operation()
        s['approvals'] = [dict(id='release', label='External release', status='pending')]
        run = await self.finish(self.coordinator.create(s, 'replay'))
        self.assertEqual(run['status'], 'unresolved')
        self.assertFalse(run['consensus'])
        self.assertNotIn('applied_plan', run)
        self.assertTrue(any(not c['passed'] for c in run['checks']))
        self.assertIn('External release', run['error'])
        self.assertIsNotNone(run['timing']['completed_at'])

    async def test_unanimous_model_acceptance_does_not_bypass_final_failed_checks(self):
        s = operation()
        class InvalidateOnCommit(FakeClient):
            async def respond(inner, context):
                reply = await super().respond(context)
                if context['round'] == 2 and context['participant']['id'] == 'pilot-A':
                    context['private_information']['aircraft']['latest_arrival'] = 0
                return reply
        self.coordinator.client_factory = InvalidateOnCommit
        run = await self.finish(self.coordinator.create(s))
        self.assertEqual(len([m for m in run['messages'] if m['round'] == 2 and m['type'] == 'ACCEPT']), len(run['participants']))
        self.assertFalse(run['consensus'])
        self.assertNotIn('applied_plan', run)

    async def test_early_search_failure_still_records_full_runtime(self):
        s = operation()
        s['resources']['runways'][0]['closures'] = [dict(start=0, end=120)]
        run = await self.finish(self.coordinator.create(s, 'replay'))
        self.assertEqual(run['status'], 'unresolved')
        self.assertEqual(run['round'], 0)
        self.assertIsNotNone(run['timing']['completed_at'])
        self.assertGreaterEqual(run['timing']['elapsed_seconds'], 0)

    async def test_provider_failure_still_records_full_runtime(self):
        class ErrorClient(FakeClient):
            async def respond(self, context):
                raise ValueError('invalid provider response')
        self.coordinator.client_factory = ErrorClient
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['status'], 'fallback')
        self.assertIsNotNone(run['timing']['completed_at'])
        self.assertEqual(run['metrics']['coordination_seconds'], run['timing']['elapsed_seconds'])

    async def test_runtime_and_automatic_application_are_saved_before_cleanup(self):
        store = self.store
        class InspectDuringCleanup(FakeClient):
            async def close(inner):
                saved = store.list()[0]
                self.assertIsNotNone(saved['timing']['completed_at'])
                self.assertEqual(saved['status'], 'applied')
                self.assertEqual(saved['decision']['decision'], 'auto_apply')
        self.coordinator.client_factory = InspectDuringCleanup
        run = await self.finish(self.coordinator.create(operation()))
        self.assertEqual(run['status'], 'applied')
        self.assertIn('applied_plan', run)

    async def test_disruption_invalidates_prior_commitments(self):
        run = await self.finish(self.coordinator.create(operation(), 'replay'))
        self.assertEqual(run['status'], 'applied')
        updated = self.coordinator.disrupt(run, dict(type='stand_unavailable', stand_id='S1', available_from=90))
        self.assertEqual(updated['scenario']['version'], 2)
        self.assertEqual(updated['episode'], 2)
        self.assertIsNone(updated['proposal'])
        self.assertFalse(updated['consensus'])
        self.assertNotIn('applied_plan', updated)
        self.assertNotIn('decision', updated)
        self.assertNotIn('baseline', updated)
        self.assertIsNone(updated['timing']['elapsed_seconds'])
        self.assertIsNotNone(updated['episodes'][0]['timing']['completed_at'])
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

    def test_illustrative_preferences_follow_outcomes_instead_of_candidate_position(self):
        s = operation()
        plans = generate_candidates(s, 12, 2)
        for person in participants(s):
            def score(p):
                if person['role'] == 'pilot':
                    f = next(f for f in s['flights'] if f['id'] == person['flight_id'])
                    a = next(a for a in p['assignments'] if a['flight_id'] == f['id'])
                    return (a['arrival']-f['reference_arrival'], a['in_block']-a['arrival']-f['taxi_in'], max(0,a['departure']-f['scheduled_departure']))
                m = p['metrics']
                return (m['surface_wait'],m['overtime'],m['total_delay']) if person['role']=='ground' else (m['airborne_wait'],m['total_delay'],m['surface_wait'])
            reordered = sorted(plans, key=score, reverse=True)
            context = context_for(person, s, reordered, [], None, 1)
            reply = illustrative_reply(person, context)
            selected = next(p for p in plans if p['id'] == reply['preferred_plan_id'])
            self.assertEqual(score(selected), min(map(score, plans)))

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

    def test_manual_approval_endpoint_is_retired(self):
        s = operation()
        p = generate_candidates(s, 1, 1)[0]
        self.app.state.store.save(dict(id='fallback', status='fallback', mode='live', scenario=s,
                                      proposal=p, episode=1, consensus=False))
        response = self.client.post('/api/runs/fallback/decision', json=dict(proposal_id=p['id'], scenario_version=1, decision='approve'))
        self.assertEqual(response.status_code, 410)
        saved = self.app.state.store.get('fallback')
        self.assertEqual(saved['status'], 'fallback')
        self.assertNotIn('applied_plan', saved)

    def test_saved_replay_preserves_runtime_and_does_not_apply_again(self):
        s = operation()
        p = generate_candidates(s, 1, 1)[0]
        timing = dict(started_at='2026-10-31T00:00:00Z', completed_at='2026-10-31T00:00:12Z', elapsed_seconds=12.0)
        decision = dict(by='Multi-agent coordinator', decision='auto_apply', at=timing['completed_at'])
        self.app.state.store.save(dict(id='timed', status='applied', mode='live', scenario=s, episode=1,
                                      created_at=timing['started_at'], timing=timing, proposal=p, applied_plan=p, decision=decision, consensus=True))
        replay = self.client.post('/api/runs', json=dict(mode='replay', scenario_id='test', replay_source_id='timed')).json()
        self.assertEqual(replay['timing'], timing)
        self.assertEqual(replay['decision'], decision)
        self.assertEqual(replay['mode'], 'recorded_replay')
        self.assertEqual(self.app.state.store.get('timed')['decision'], decision)

    def test_pending_external_approval_does_not_allow_manual_application(self):
        s = operation()
        p = generate_candidates(s, 1, 1)[0]
        s['approvals'] = [dict(id='release', status='pending')]
        self.app.state.store.save(dict(id='blocked', status='unresolved', mode='live', scenario=s,
                                      proposal=p, episode=1, consensus=False))
        response = self.client.post('/api/runs/blocked/decision', json=dict(proposal_id=p['id'], scenario_version=1, decision='approve'))
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('applied_plan', self.app.state.store.get('blocked'))

    def test_incomplete_disruption_is_validation_error(self):
        r = self.client.post('/api/runs/unknown/disruptions', json={'type': 'runway_closure'})
        self.assertEqual(r.status_code, 422)

    def test_recorded_replay_cannot_replace_a_different_selected_scenario(self):
        self.app.state.store.save(dict(id='saved', status='approved', mode='replay', scenario=operation(),
                                      episode=1, created_at='2026-10-31T00:00:00Z', consensus=True))
        mismatch = self.client.post('/api/runs', json=dict(mode='replay', scenario_id='infeasible', replay_source_id='saved'))
        self.assertEqual(mismatch.status_code, 409)
        self.assertIn('selected scenario', mismatch.json()['detail'])
        same = self.client.post('/api/runs', json=dict(mode='replay', scenario_id='test', replay_source_id='saved'))
        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.json()['scenario']['id'], 'test')
        listed = self.client.get('/api/runs').json()['runs']
        self.assertTrue(all(r['scenario_id'] == 'test' for r in listed))


if __name__ == '__main__':
    unittest.main()
