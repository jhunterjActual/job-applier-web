"""Compatible Claude disablement leaves saved state readable and makes no calls."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app
import ai_providers as providers
import claude_adapter as claude


class ClaudeRecoveryTests(unittest.TestCase):
    def test_disabled_structured_ocr_capability_and_direct_transport_never_dispatch(self):
        settings = providers.AIProviderSettings('anthropic', claude.MODEL, 'synthetic-key')
        with patch.object(claude, 'CLAUDE_ENABLED', False), \
                patch.object(claude.urllib.request, 'build_opener') as transport, \
                patch.object(providers, '_generate_gemini') as gemini, \
                patch.object(providers, '_generate_openai') as openai:
            calls = [
                lambda: providers.generate_structured(settings, 'Synthetic', providers.CapabilityResponse),
                lambda: providers.extract_text_from_images(settings, [b'synthetic-png']),
                lambda: providers.validate_provider_capability(settings),
                lambda: claude._message(settings, 'Synthetic', 100, 30),
            ]
            for call in calls:
                with self.assertRaises(providers.AIProviderError) as caught:
                    call()
                self.assertEqual('model_unavailable', caught.exception.code)
            transport.assert_not_called()
            gemini.assert_not_called()
            openai.assert_not_called()

    def test_disabled_readback_preserves_profile_and_encrypted_secret_status(self):
        saved = {'ai_provider': 'anthropic', 'ai_model': claude.MODEL, 'name': 'Synthetic'}
        baseline = copy.deepcopy(saved)
        repository = Mock()
        repository.get_profile.return_value = saved
        with patch.object(claude, 'CLAUDE_ENABLED', False), \
                patch.object(app, 'runtime_profile_repository', return_value=repository), \
                patch.object(app, 'provider_secret_store_kind', return_value=app.SHARED_SECRET_STORE), \
                patch.object(app, 'provider_key_configured', side_effect=lambda row, provider: provider == 'anthropic'), \
                patch.object(app, 'current_provider_secret_configured', return_value=False), \
                patch.object(app, 'maps_provider_ready', return_value=True), \
                patch.object(app, 'update_provider_secrets') as write:
            result = app.get_profile()
            self.assertEqual('anthropic', result['ai_provider'])
            self.assertEqual(claude.MODEL, result['ai_model'])
            self.assertTrue(result['anthropic_api_key_configured'])
            self.assertFalse(result['anthropic_available'])
            self.assertNotIn('anthropic_api_key', result)
            self.assertEqual(baseline, saved)
            repository.update_profile.assert_not_called()
            write.assert_not_called()

    def test_disabled_admission_rejects_before_any_profile_or_mixed_secret_write(self):
        profile = app.ProfileUpdate(name='Synthetic', email='', phone='', github='', linkedin='', website='',
            base_resume_text='Synthetic resume', ai_provider='anthropic', ai_model=claude.MODEL)
        request = SimpleNamespace(state=SimpleNamespace(local_user_id=42))
        with patch.object(claude, 'CLAUDE_ENABLED', False), \
                patch.object(app, 'runtime_profile_repository') as repository, \
                patch.object(app, 'update_provider_secrets') as write, \
                patch.object(app, 'get_db_connection') as database:
            with self.assertRaises(app.HTTPException) as caught:
                app.update_profile(profile)
            self.assertEqual(422, caught.exception.status_code)
            for value in ('synthetic-replacement', ''):
                with self.assertRaises(app.HTTPException):
                    app.update_profile_secrets(app.ProfileSecretsUpdate(
                        anthropic_api_key=value, openai_api_key='synthetic-other'), request)
            repository.assert_not_called()
            write.assert_not_called()
            database.assert_not_called()

    def test_enabled_recovery_switch_still_requires_hosted_storage(self):
        with patch.object(claude, 'CLAUDE_ENABLED', True), \
                patch.object(providers, 'provider_secret_store_kind', return_value='local'):
            with self.assertRaises(providers.AIProviderError) as caught:
                providers.settings_from_profile({'ai_provider': 'anthropic', 'ai_model': claude.MODEL})
            self.assertEqual('unsupported_storage', caught.exception.code)


if __name__ == '__main__':
    unittest.main()
