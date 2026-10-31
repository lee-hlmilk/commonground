import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.jev import JevAPIError, JevLowConfidenceError, JevParticipantClient, build_request, interpret_decision, validate_answers


def context(round_number=1, valid=True):
    plans = [dict(id='plan-a', valid=valid, failed_checks=[] if valid else [{'passed':False}],
                  scenario_version=1, assignments=[], metrics={}),
             dict(id='plan-b', valid=True, failed_checks=[], scenario_version=1, assignments=[], metrics={})]
    return dict(participant={'id':'pilot-A','role':'pilot','flight_id':'A'}, round=round_number,
                candidates=plans, proposal=plans[0] if round_number == 2 else None,
                scenario_version=1, evidence={'scenario:version':1,'plan:plan-a':{},'plan:plan-b':{},'flight:A:waiting_limit':20},
                private_information={'aircraft':{'id':'A'}}, received_messages=[])


def answer(request, choice=None, confidence=.9, yes=.95):
    answers = {}
    for key, q in request['questions'].items():
        if q['type'] == 'noul':
            answers[key] = {'type':'noul','noul':yes}
        else:
            labels = list(q['criteria'])
            selected = choice or ('ACCEPT' if key == 'commitment' else labels[0])
            others = (1 - .9)/(len(labels)-1)
            answers[key] = {'type':'choice','choice':selected,'confidence':confidence,
                            'probabilities':{k:.9 if k == selected else others for k in labels}}
    return dict(model='jev-1.13.0', model_version='jev-test-build', answers=answers,
                usage={'input_tokens':80,'output_tokens':0})


def settings(**overrides):
    return SimpleNamespace(**(dict(jev_api_key='secret-test-token',jev_base_url='https://api.typesafe.ai/v1',
        jev_model='jev-latest',jev_timeout=5,jev_retries=1,jev_min_confidence=.7) | overrides))


class JevValidationTests(unittest.TestCase):
    def test_round_one_preferences_and_labelled_templates(self):
        c = context()
        req = build_request(c,'jev-latest')
        result = interpret_decision(c,answer(req),req['questions'],.7)
        self.assertEqual(result['type'],'PROPOSE')
        self.assertEqual(result['acceptable_plan_ids'],['plan-a','plan-b'])
        self.assertEqual(result['preferred_plan_id'],'plan-a')
        self.assertEqual(result['statement_source'],'template_from_jev_decision')
        self.assertEqual(result['decision']['model_version'],'jev-test-build')

    def test_low_confidence_does_not_invent_preference(self):
        c=context();req=build_request(c,'jev-latest')
        result=interpret_decision(c,answer(req,confidence=.6),req['questions'],.7)
        self.assertEqual(result['type'],'REQUEST')
        self.assertEqual(result['acceptable_plan_ids'],[])
        self.assertIsNone(result['preferred_plan_id'])

    def test_preferred_plan_must_also_be_acceptable(self):
        c=context();req=build_request(c,'jev-latest')
        payload=answer(req);payload['answers']['acceptable_0']['noul']=.2
        result=interpret_decision(c,payload,req['questions'],.7)
        self.assertIsNone(result['preferred_plan_id'])
        self.assertNotIn('plan-a',result['acceptable_plan_ids'])

    def test_round_two_accepts_exact_current_proposal(self):
        c=context(2);req=build_request(c,'jev-latest')
        result=interpret_decision(c,answer(req),req['questions'],.7)
        self.assertEqual(result['type'],'ACCEPT')
        self.assertEqual(result['proposal_id'],'plan-a')

    def test_failed_external_check_blocks_model_acceptance(self):
        c=context(2,False);req=build_request(c,'jev-latest')
        result=interpret_decision(c,answer(req),req['questions'],.7)
        self.assertEqual(result['type'],'UNABLE')
        self.assertEqual(result['acceptable_plan_ids'],[])

    def test_uncertain_round_two_escalates(self):
        c=context(2);req=build_request(c,'jev-latest')
        self.assertEqual(interpret_decision(c,answer(req,confidence=.3),req['questions'],.7)['type'],'ESCALATE')

    def test_malformed_distributions_are_rejected(self):
        req=build_request(context(),'jev-latest')
        for bad in ('unknown','missing','nan','negative','unnormalised','nonmax','boolean','confidence'):
            payload=answer(req);a=payload['answers']['preferred']
            if bad=='unknown':a['choice']='made-up'
            if bad=='missing':a['probabilities'].pop('plan-b')
            if bad=='nan':a['probabilities']['plan-a']=float('nan')
            if bad=='negative':a['probabilities']['plan-a']=-1
            if bad=='unnormalised':a['probabilities']['plan-a']=.2
            if bad=='nonmax':a['choice']='plan-b'
            if bad=='boolean':a['probabilities']['plan-a']=True
            if bad=='confidence':a['confidence']=float('inf')
            with self.subTest(bad=bad),self.assertRaises(JevAPIError):
                validate_answers(payload,req['questions'])

    def test_answer_shape_and_usage_are_validated(self):
        req=build_request(context(),'jev-latest')
        for bad in ('missing','extra','wrongtype','noul','usage'):
            payload=answer(req)
            if bad=='missing':payload['answers'].pop('acceptable_0')
            if bad=='extra':payload['answers']['unexpected']={}
            if bad=='wrongtype':payload['answers']['acceptable_0']['type']='choice'
            if bad=='noul':payload['answers']['acceptable_0']['noul']=True
            if bad=='usage':payload['usage']['input_tokens']=-1
            with self.subTest(bad=bad),self.assertRaises(JevAPIError):
                validate_answers(payload,req['questions'])

    def test_state_guard_rejects_instead_of_truncating(self):
        c=context();c['received_messages']=['x'*100_000]
        with self.assertRaises(JevAPIError):build_request(c,'jev-latest')


class JevHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self,handler,c=None,**config):
        client=JevParticipantClient(settings(**config),httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:return await client.respond(c or context())
        finally:await client.close()

    async def test_auth_endpoint_usage_and_no_claude_request(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,headers={'x-request-id':'jev-request'},json=answer(json.loads(request.content)))
        result=await self.invoke(handler)
        self.assertEqual(str(requests[0].url),'https://api.typesafe.ai/v1/systemone')
        self.assertEqual(requests[0].headers['authorization'],'Bearer secret-test-token')
        sent=json.loads(requests[0].content)
        self.assertEqual(set(sent),{'model','state','questions'})
        self.assertEqual(result['usage']['request_id'],'jev-request')
        self.assertEqual(result['usage']['provider'],'jev')

    async def test_transient_error_retries_once(self):
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(429,headers={'retry-after':'0'}) if len(calls)==1 else httpx.Response(200,json=answer(json.loads(request.content)))
        result=await self.invoke(handler)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['type'],'PROPOSE')

    async def test_auth_and_validation_errors_never_retry_or_leak_body(self):
        for status in (401,422):
            calls=[]
            def handler(request):
                calls.append(request)
                return httpx.Response(status,text='secret-test-token and confidential state')
            with self.subTest(status=status),self.assertRaises(JevAPIError) as caught:
                await self.invoke(handler)
            self.assertEqual(len(calls),1)
            self.assertNotIn('secret-test-token',str(caught.exception))
            self.assertNotIn('confidential',str(caught.exception))

    async def test_malformed_json_is_not_a_commitment(self):
        with self.assertRaises(JevAPIError):
            await self.invoke(lambda request:httpx.Response(200,text='{"model":'))

    async def test_timeout_retry_is_bounded(self):
        calls=[]
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout('secret-test-token',request=request)
        with patch('backend.jev.asyncio.sleep',new=AsyncMock()),self.assertRaises(JevAPIError) as caught:
            await self.invoke(handler)
        self.assertEqual(len(calls),2)
        self.assertNotIn('secret-test-token',str(caught.exception))

    async def test_bad_round_two_acceptance_stays_blocked_over_http(self):
        result=await self.invoke(lambda request:httpx.Response(200,json=answer(json.loads(request.content))),context(2,False))
        self.assertEqual(result['type'],'UNABLE')

    async def test_low_preference_confidence_signals_cascade_with_audit_metadata(self):
        with self.assertRaises(JevLowConfidenceError) as caught:
            await self.invoke(lambda request:httpx.Response(200,headers={'request-id':'low-confidence'},
                json=answer(json.loads(request.content),confidence=.3)))
        error=caught.exception
        self.assertEqual(error.reason,'preferred_choice_confidence')
        self.assertEqual(error.usage['request_id'],'low-confidence')
        self.assertEqual(error.decision['answers']['preferred']['confidence'],.3)
        self.assertEqual(error.jev_result['statement_source'],'template_from_jev_decision')

    async def test_low_selected_plan_acceptability_signals_cascade(self):
        def handler(request):
            payload=answer(json.loads(request.content))
            payload['answers']['acceptable_0']['noul']=.5
            return httpx.Response(200,json=payload)
        with self.assertRaises(JevLowConfidenceError) as caught:
            await self.invoke(handler)
        self.assertEqual(caught.exception.reason,'preferred_plan_acceptability')

    async def test_low_commitment_confidence_signals_cascade(self):
        with self.assertRaises(JevLowConfidenceError) as caught:
            await self.invoke(lambda request:httpx.Response(200,json=answer(json.loads(request.content),confidence=.4)),context(2))
        self.assertEqual(caught.exception.reason,'commitment_confidence')

    async def test_confident_review_and_unable_do_not_trigger_cascade(self):
        for c,choice,expected in ((context(),'REVIEW','REQUEST'),(context(2),'UNABLE','UNABLE')):
            result=await self.invoke(lambda request:httpx.Response(200,json=answer(json.loads(request.content),choice=choice)),c)
            self.assertEqual(result['type'],expected)

    async def test_unchosen_low_acceptability_does_not_trigger_cascade(self):
        def handler(request):
            payload=answer(json.loads(request.content))
            payload['answers']['acceptable_1']['noul']=.1
            return httpx.Response(200,json=payload)
        result=await self.invoke(handler)
        self.assertEqual(result['preferred_plan_id'],'plan-a')
        self.assertEqual(result['acceptable_plan_ids'],['plan-a'])

    async def test_invalid_selected_plan_is_not_a_confidence_cascade(self):
        def handler(request):
            payload=answer(json.loads(request.content))
            payload['answers']['acceptable_0']['noul']=.1
            return httpx.Response(200,json=payload)
        result=await self.invoke(handler,context(valid=False))
        self.assertEqual(result['type'],'REQUEST')


if __name__=='__main__':unittest.main()
