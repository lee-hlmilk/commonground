from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.agents import participant_client
from backend.config import Settings
from backend.main import create_app
from backend.providers import JevFirstParticipantClient


class ProviderConfigTests(unittest.TestCase):
    def test_settings_select_primary_and_validate_route(self):
        settings = Settings(provider='jev', jev_api_key='jev-test', api_key='claude-test', jev_model='jev-test-model')
        self.assertEqual(settings.selected_api_key, 'jev-test')
        self.assertEqual(settings.selected_model, 'jev-test-model')
        self.assertEqual(settings.provider_label, 'Jev')
        self.assertEqual(Settings(max_rounds=3).max_rounds, 3)
        with self.assertRaises(ValueError):
            Settings(max_rounds=4)
        for kwargs in ({'provider':'typo'}, {'jev_min_confidence':.5},
                       {'jev_base_url':'http://untrusted.example/v1'},
                       {'jev_base_url':'https://example.test/v1?api_key=secret'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_health_and_missing_primary_are_provider_specific(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(provider='jev', jev_api_key='', api_key='claude-present',
                                database=str(Path(folder)/'runs.sqlite'))
            repository = type('Repo', (), {})()
            with TestClient(create_app(settings, repository)) as client:
                health = client.get('/api/health').json()
                self.assertEqual(health['provider'], 'jev')
                self.assertFalse(health['provider_configured'])
                self.assertTrue(health['fallback_configured'])
                self.assertEqual(health['fallback_provider'], 'claude')
                self.assertNotIn('claude-present', str(health))
                response = client.post('/api/runs', json={'mode':'live'})
                self.assertEqual(response.status_code, 400)
                self.assertIn('JEV_API_KEY', response.json()['detail'])

    def test_env_example_defaults_to_jev_and_contains_both_blank_keys(self):
        text = (Path(__file__).resolve().parents[1]/'.env.example').read_text()
        self.assertIn('DECISION_PROVIDER=jev',text)
        self.assertIn('JEV_API_KEY=\n',text)
        self.assertIn('ANTHROPIC_API_KEY=\n',text)
