"""Exercise the real Anthropic SDK against a local in-memory HTTP transport."""
import json
import unittest

import httpx
from anthropic import AsyncAnthropic

from backend.agents import ClaudeParticipantClient, context_for, illustrative_reply, participants
from backend.config import Settings
from backend.solver import generate_candidates
from tests.test_coordination import operation


class ClaudeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_serializes_supported_schema_and_validates_response(self):
        s = operation()
        plans = generate_candidates(s, 2, 1)
        person = participants(s)[1]
        context = context_for(person, s, plans, [], None, 1)
        answer = illustrative_reply(person, context)
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, headers={'request-id': 'test-request'}, json={
                'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'test-model',
                'content': [{'type': 'text', 'text': json.dumps(answer)}],
                'stop_reason': 'end_turn', 'stop_sequence': None,
                'usage': {'input_tokens': 40, 'output_tokens': 30}})

        adapter = ClaudeParticipantClient(Settings(api_key='test-key'))
        await adapter.client.close()
        adapter.client = AsyncAnthropic(api_key='test-key', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            reply = await adapter.respond(context)
        finally:
            await adapter.close()
        self.assertEqual(reply['usage']['request_id'], 'test-request')
        self.assertEqual(sent[0]['output_config']['format']['type'], 'json_schema')
        self.assertNotIn('maxLength', sent[0]['output_config']['format']['schema']['properties']['statement'])
        self.assertEqual(json.loads(sent[0]['messages'][0]['content'])['participant']['id'], person['id'])

    async def test_truncated_response_cannot_be_accepted(self):
        def handler(request):
            return httpx.Response(200, json={
                'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'test-model',
                'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'max_tokens',
                'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        adapter = ClaudeParticipantClient(Settings(api_key='test-key'))
        await adapter.client.close()
        adapter.client = AsyncAnthropic(api_key='test-key', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with self.assertRaises(ValueError):
                await adapter.respond({'participant': {'role': 'pilot'}})
        finally:
            await adapter.close()
