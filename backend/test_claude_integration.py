"""Claude profile and workflow integration using synthetic keys and fake I/O."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
import ai_providers as providers
import ai_contracts as contracts
import claude_adapter as claude
import searcher
import tailor
from test_claude_adapter import reply
from test_ai_contracts import HTTPResponse


class ClaudeIntegrationTests(unittest.TestCase):
    def profile(self, **extra):
        return app.ProfileUpdate(name='Synthetic', email='', phone='', github='', linkedin='', website='',
                                 base_resume_text='Synthetic resume', ai_provider='anthropic',
                                 ai_model=extra.get('model', claude.MODEL))

    def test_local_rejects_claude_before_profile_or_any_secret_mutation(self):
        with patch.object(app, 'provider_secret_store_kind', return_value='local'), \
                patch.object(app, 'runtime_profile_repository') as repository, \
                patch.object(app, 'get_db_connection') as database:
            with self.assertRaises(app.HTTPException) as caught:
                app.update_profile(self.profile())
            self.assertEqual(422, caught.exception.status_code)
            with self.assertRaises(app.HTTPException):
                app.update_profile_secrets(app.ProfileSecretsUpdate(anthropic_api_key='PRIVATE',
                                                                   openai_api_key='OTHER-PRIVATE'))
            repository.assert_not_called()
            database.assert_not_called()

    def test_hosted_profile_admission_and_model_gate(self):
        repository = Mock()
        repository.update_profile.return_value = {}
        with patch.object(app, 'provider_secret_store_kind', return_value=app.SHARED_SECRET_STORE), \
                patch.object(app, 'runtime_profile_repository', return_value=repository):
            self.assertTrue(app.update_profile(self.profile())['success'])
            self.assertEqual('anthropic', repository.update_profile.call_args.args[0]['ai_provider'])
            with self.assertRaises(app.HTTPException):
                app.update_profile(self.profile(model='unreviewed-model'))
            self.assertEqual(1, repository.update_profile.call_count)

    def test_hosted_secret_update_only_supplied_provider_and_boolean_readback(self):
        request = SimpleNamespace(state=SimpleNamespace(local_user_id=123))
        with patch.object(app, 'provider_secret_store_kind', return_value=app.SHARED_SECRET_STORE), \
                patch.object(app, 'update_provider_secrets') as save, \
                patch.object(app, 'current_provider_secret_configured', return_value=True):
            for value in ('PRIVATE', ''):
                result = app.update_profile_secrets(app.ProfileSecretsUpdate(anthropic_api_key=value), request)
                save.assert_called_with(123, {'anthropic': value})
                self.assertTrue(result['anthropic_api_key_configured'])
                self.assertNotIn('PRIVATE', json.dumps(result))
            save.reset_mock()
            with self.assertRaises(app.HTTPException) as caught:
                app.update_profile_secrets(app.ProfileSecretsUpdate(anthropic_api_key='PRIVATE'))
            self.assertEqual(401, caught.exception.status_code)
            save.assert_not_called()

    def test_profile_readback_never_returns_raw_claude_key(self):
        repository = Mock()
        repository.get_profile.return_value = {'ai_provider': 'anthropic', 'ai_model': claude.MODEL,
                                              'anthropic_api_key': 'PRIVATE'}
        with patch.object(app, 'runtime_profile_repository', return_value=repository), \
                patch.object(app, 'provider_key_configured', return_value=True), \
                patch.object(app, 'provider_secret_store_kind', return_value=app.SHARED_SECRET_STORE), \
                patch.object(app, 'current_provider_secret_configured', return_value=False), \
                patch.object(app, 'maps_provider_ready', return_value=True):
            result = app.get_profile()
        self.assertNotIn('anthropic_api_key', result)
        self.assertTrue(result['anthropic_api_key_configured'])
        self.assertTrue(result['anthropic_available'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_settings_and_keyless_callers_use_only_encrypted_claude_key(self):
        with patch.object(providers, 'provider_secret_store_kind', return_value=providers.SHARED_SECRET_STORE), \
                patch.object(providers, 'current_provider_secret', return_value='PRIVATE') as secret, \
                patch.object(providers.config, 'get_gemini_api_key') as gemini, \
                patch.object(providers.config, 'get_openai_api_key') as openai:
            settings = providers.settings_from_profile({'ai_provider': 'anthropic', 'ai_model': claude.MODEL})
            self.assertEqual('PRIVATE', settings.api_key)
            self.assertEqual('PRIVATE', tailor._provider_settings(None, 'anthropic', claude.MODEL).api_key)
            with patch.object(searcher, 'generate_structured', return_value={'titles': ['Engineer']}) as generate:
                self.assertEqual(['Engineer'], searcher.extract_search_keywords_from_resume('Synthetic', ai_provider='anthropic'))
                self.assertEqual('PRIVATE', generate.call_args.args[0].api_key)
            self.assertTrue(all(call.args == ('anthropic',) for call in secret.call_args_list))
            gemini.assert_not_called()
            openai.assert_not_called()

    def test_real_scoring_query_and_validation_dispatch_through_claude(self):
        opener = Mock()
        with patch.object(claude.urllib.request, 'build_opener', return_value=opener):
            with contracts.capture_receipts() as records:
                opener.open.return_value = HTTPResponse(reply('{"match_score":75,"match_analysis":"Synthetic"}'))
                result = tailor.analyze_job_match('Synthetic resume', 'Engineer', 'Example', 'Synthetic job',
                                                  api_key='PRIVATE', ai_provider='anthropic')
                self.assertTrue(result['success'])
                opener.open.return_value = HTTPResponse(reply('{"titles":["Engineer"]}'))
                self.assertEqual(['Engineer'], searcher.extract_search_keywords_from_resume('Synthetic',
                                                                 api_key='PRIVATE', ai_provider='anthropic'))
                opener.open.return_value = HTTPResponse(reply('{"ready":true}'))
                settings = providers.AIProviderSettings('anthropic', claude.MODEL, 'PRIVATE')
                self.assertEqual('anthropic', providers.validate_provider_capability(settings)['provider'])
                opener.open.return_value = HTTPResponse(reply('Synthetic page'))
                self.assertEqual(['Synthetic page'], providers.extract_text_from_images(settings, [b'page']))
            self.assertEqual(['scoring', 'query_preparation', 'capability_check', 'resume_ocr'],
                             [record.operation for record in records])
            self.assertEqual(4, opener.open.call_count)
            self.assertNotIn('PRIVATE', repr(records))
