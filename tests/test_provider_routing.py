"""Provider cascade is per response; transport errors are not confidence."""
import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from backend.agents import context_for, illustrative_reply, participants, validate_reply
from backend.config import Settings
from backend.coordination import Coordinator
from backend.jev import JevAPIError, JevLowConfidenceError
from backend.providers import JevFirstParticipantClient
from backend.solver import generate_candidates
from backend.store import RunStore
from tests.test_coordination import operation


def reply_for(context):
    result = validate_reply(illustrative_reply(context['participant'], context), context)
    result.update(provider='jev', statement_source='template_from_jev_decision')
    return result


def low_confidence(context):
    result = reply_for(context)
    result.update(decision={'answers': {'preferred': {'confidence': .4}},
                           'model': 'jev-test', 'model_version': 'test-build', 'confidence_threshold': .7},
                  usage={'provider': 'jev', 'model': 'jev-test', 'input_tokens': 10, 'output_tokens': 0})
    return JevLowConfidenceError(result, 'preferred_choice_confidence')


def client(respond=None):
    instance = Mock()
    instance.respond = AsyncMock(side_effect=respond) if respond else AsyncMock()
    instance.close = AsyncMock()
    return instance


class ProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(provider='jev', jev_api_key='test-jev-key', api_key='test-claude-key')
        self.scenario = operation()
        self.plans = generate_candidates(self.scenario, 2, 1)
        self.person = participants(self.scenario)[1]
        self.context = context_for(self.person, self.scenario, self.plans, [], None, 1)

    async def test_confident_primary_never_constructs_claude(self):
        primary = client(lambda context: reply_for(context))
        factory = Mock()
        cascade = JevFirstParticipantClient(self.settings, primary, factory)
        result = await cascade.respond(self.context)
        self.assertEqual(result['provider'], 'jev')
        factory.assert_not_called()
        await cascade.close()
        primary.close.assert_awaited_once()

    async def test_low_confidence_handoff_keeps_same_role_round_and_audit(self):
        primary = client(lambda context: (_ for _ in ()).throw(low_confidence(context)))
        fallback = client(lambda context: reply_for(context))
        factory = Mock(return_value=fallback)
        cascade = JevFirstParticipantClient(self.settings, primary, factory)
        before = copy.deepcopy(self.context)
        result = await cascade.respond(self.context)
        sent = fallback.respond.await_args.args[0]
        self.assertEqual(sent['participant'], before['participant'])
        self.assertEqual(sent['round'], before['round'])
        self.assertEqual(sent['private_information'], before['private_information'])
        self.assertEqual(sent['decision_handoff']['reason'], 'low_confidence')
        self.assertEqual(self.context, before)
        self.assertEqual(result['provider'], 'claude')
        self.assertEqual(result['fallback']['from'], 'jev')
        self.assertEqual(result['primary_usage']['provider'], 'jev')
        self.assertEqual(result['primary_decision']['model_version'], 'test-build')
        self.assertGreaterEqual(result['cascade_seconds'], 0)
        await cascade.close()
        primary.close.assert_awaited_once()
        fallback.close.assert_awaited_once()

    async def test_fallback_client_is_reused_but_role_contexts_stay_separate(self):
        primary = client(lambda context: (_ for _ in ()).throw(low_confidence(context)))
        fallback = client(lambda context: reply_for(context))
        factory = Mock(return_value=fallback)
        cascade = JevFirstParticipantClient(self.settings, primary, factory)
        await cascade.respond(self.context)
        other = context_for(participants(self.scenario)[2], self.scenario, self.plans, [], None, 1)
        await cascade.respond(other)
        factory.assert_called_once()
        contexts = [call.args[0] for call in fallback.respond.await_args_list]
        self.assertNotEqual(contexts[0]['participant']['id'], contexts[1]['participant']['id'])
        await cascade.close()

    async def test_missing_claude_key_preserves_safe_handoff(self):
        settings = Settings(provider='jev', jev_api_key='test-jev-key', api_key='')
        primary = client(lambda context: (_ for _ in ()).throw(low_confidence(context)))
        factory = Mock()
        cascade = JevFirstParticipantClient(settings, primary, factory)
        with self.assertRaises(JevAPIError) as caught:
            await cascade.respond(self.context)
        self.assertIn('ANTHROPIC_API_KEY', caught.exception.safe_message)
        self.assertEqual(caught.exception.handoff['participant'], self.person['id'])
        self.assertEqual(caught.exception.handoff['round'], 1)
        self.assertIn('primary_usage', caught.exception.handoff)
        factory.assert_not_called()
        await cascade.close()

    async def test_confident_unable_is_not_overridden(self):
        refusal = reply_for(self.context)
        refusal.update(type='UNABLE', acceptable_plan_ids=[], preferred_plan_id=None)
        primary = client(lambda context: copy.deepcopy(refusal))
        factory = Mock()
        cascade = JevFirstParticipantClient(self.settings, primary, factory)
        self.assertEqual((await cascade.respond(self.context))['type'], 'UNABLE')
        factory.assert_not_called()
        await cascade.close()

    async def test_final_turn_reviews_prior_escalation_without_rewriting_it(self):
        proposal = self.plans[0]
        c = context_for(self.person, self.scenario, self.plans, [], proposal, 3)
        previous = dict(provider='jev', type='ESCALATE', round=2, proposal_id=proposal['id'],
                        decision={'answers': {'commitment': {'choice': 'ESCALATE'}}}, usage={'model': 'jev-test'})
        c['reconciliation'] = dict(previous_reply=previous, proposal_changed=False)
        primary = client()
        fallback = client(lambda context: reply_for(context))
        cascade = JevFirstParticipantClient(self.settings, primary, Mock(return_value=fallback))
        result = await cascade.respond(c)
        self.assertEqual(result['type'], 'ACCEPT')
        self.assertEqual(result['review']['reason'], 'final_turn_escalation_review')
        self.assertEqual(result['primary_decision'], previous['decision'])
        self.assertEqual(previous['type'], 'ESCALATE')
        primary.respond.assert_not_awaited()
        self.assertEqual(fallback.respond.await_args.args[0]['round'], 3)
        await cascade.close()

    async def test_final_review_does_not_bypass_refusal_or_failed_checks(self):
        for kind, valid in [('UNABLE', True), ('ESCALATE', False)]:
            c = context_for(self.person, self.scenario, self.plans, [], self.plans[0], 3)
            c['proposal_assessment']['all_checks_pass'] = valid
            c['reconciliation'] = dict(previous_reply=dict(provider='jev', type=kind, round=2, proposal_id=self.plans[0]['id']))
            primary = client(lambda context: dict(reply_for(context), type=kind, acceptable_plan_ids=[], preferred_plan_id=None))
            factory = Mock()
            cascade = JevFirstParticipantClient(self.settings, primary, factory)
            result = await cascade.respond(c)
            self.assertEqual(result['type'], kind)
            factory.assert_not_called()
            await cascade.close()

    async def test_jev_http_error_does_not_trigger_claude(self):
        for status in (401, 422, 429, 529):
            primary = client(lambda context: (_ for _ in ()).throw(JevAPIError('Safe provider failure', status)))
            factory = Mock()
            cascade = JevFirstParticipantClient(self.settings, primary, factory)
            with self.subTest(status=status), self.assertRaises(JevAPIError):
                await cascade.respond(self.context)
            factory.assert_not_called()
            await cascade.close()

    async def test_claude_error_is_sanitized_and_handoff_retained(self):
        primary = client(lambda context: (_ for _ in ()).throw(low_confidence(context)))
        fallback = client(lambda context: (_ for _ in ()).throw(ValueError('SECRET provider response body')))
        cascade = JevFirstParticipantClient(self.settings, primary, Mock(return_value=fallback))
        with self.assertRaises(JevAPIError) as caught:
            await cascade.respond(self.context)
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertIn('validation', caught.exception.safe_message)
        self.assertEqual(caught.exception.handoff['primary_decision']['model'], 'jev-test')
        await cascade.close()

    async def test_close_fallback_even_when_primary_close_fails(self):
        primary = client()
        primary.close.side_effect = RuntimeError('close failed')
        cascade = JevFirstParticipantClient(self.settings, primary)
        cascade.fallback = client()
        with self.assertRaises(RuntimeError):
            await cascade.close()
        cascade.fallback.close.assert_awaited_once()


class MixedProviderCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.tmp.name) / 'routing.sqlite')
        self.settings = Settings(provider='jev', jev_api_key='test-jev-key', api_key='test-claude-key')
        self.cascades = []

    async def asyncTearDown(self):
        for task in self.coordinator.tasks.values():
            task.cancel()
        await asyncio.gather(*self.coordinator.tasks.values(), return_exceptions=True)
        self.tmp.cleanup()

    def factory(self, settings):
        def primary_response(context):
            if context['participant']['id'] == 'pilot-A':
                raise low_confidence(context)
            return reply_for(context)
        primary = client(primary_response)
        fallback = client(lambda context: reply_for(context))
        cascade = JevFirstParticipantClient(settings, primary, Mock(return_value=fallback))
        self.cascades.append(cascade)
        return cascade

    async def finish(self, scenario):
        self.coordinator = Coordinator(self.store, self.settings, self.factory)
        run = self.coordinator.create(scenario)
        await self.coordinator.tasks[run['id']]
        return self.store.get(run['id'])

    async def test_mixed_providers_agree_within_same_two_rounds(self):
        run = await self.finish(operation())
        self.assertEqual(run['status'], 'applied')
        self.assertTrue(run['consensus'])
        self.assertEqual(run['round'], 2)
        self.assertEqual(len(run['messages']), len(run['participants']) * 2)
        self.assertEqual({m['provider'] for m in run['messages']}, {'jev', 'claude'})
        cascade = self.cascades[0]
        self.assertEqual(cascade.primary.respond.await_count, len(run['participants']) * 2)
        self.assertEqual(cascade.fallback.respond.await_count, 2)
        self.assertTrue(all(m['sender'] == 'pilot-A' for m in run['messages'] if m['provider'] == 'claude'))

    async def test_external_constraint_remains_blocking_with_mixed_providers(self):
        scenario = operation()
        scenario['approvals'] = [{'id':'airport-release', 'status':'pending'}]
        run = await self.finish(scenario)
        self.assertFalse(run['consensus'])
        self.assertEqual(run['status'], 'unresolved')
        self.assertEqual(run['round'], 2)
        self.assertTrue(any(not check['passed'] for check in run['checks']))


if __name__ == '__main__':
    unittest.main()
