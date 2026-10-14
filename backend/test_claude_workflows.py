"""Synthetic caller acceptance for Claude; run with an isolated data directory."""
import base64
import json
import unittest
from unittest.mock import Mock, patch

import pymupdf
import ai_contracts as contracts
import ai_providers as providers
import claude_adapter as claude
import interview_prep
import maps_providers
import provider_secrets
import resume_documents
import search_run_execution as execution
from search_run_repository import SearchRunClaim
import tailor
from test_ai_contracts import HTTPResponse
from test_claude_adapter import reply


class ClaudeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.settings = providers.AIProviderSettings('anthropic', claude.MODEL, 'PRIVATE-CLAUDE')
        self.opener = Mock()
        self.transport = patch.object(claude.urllib.request, 'build_opener', return_value=self.opener)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        gemini_patch = patch.object(providers, '_generate_gemini')
        openai_patch = patch.object(providers, '_generate_openai')
        self.gemini = gemini_patch.start()
        self.openai = openai_patch.start()
        self.addCleanup(gemini_patch.stop)
        self.addCleanup(openai_patch.stop)

    def respond(self, value):
        self.opener.open.return_value = HTTPResponse(reply(json.dumps(value)))

    def test_tailoring_interview_and_batch_use_selected_claude_transport(self):
        options = {'api_key': self.settings.api_key, 'ai_provider': 'anthropic', 'ai_model': claude.MODEL}
        with contracts.capture_receipts() as records:
            self.respond({'tailored_resume': '# Candidate\n\n## Professional Summary\nSynthetic experience.',
                          'cover_letter': 'Dear hiring team,\nSynthetic cover letter.'})
            result = tailor.tailor_resume_and_cover_letter('Synthetic resume', 'Engineer', 'Example',
                                                           'Synthetic role', **options)
            self.assertTrue(result['success'])
            self.respond({field: ['Prepare a truthful example.', 'Review the saved posting.', 'Write down your questions.']
                          for field in interview_prep.InterviewPrepAIResponse.model_fields})
            result = interview_prep.generate_interview_prep(self.settings,
                        {'company': 'Example', 'title': 'Engineer', 'description': 'Synthetic role'})
            self.assertIn('Prepare a truthful example.', result)
            self.respond({'matches': [{'index': i, 'match_score': 70 + i, 'match_analysis': 'Synthetic'}
                                      for i in range(5)]})
            result = tailor.analyze_job_matches_batch('Synthetic resume',
                        [{'title': 'Engineer', 'company': 'Example', 'description': 'Synthetic role'}] * 5,
                        **options)
            self.assertTrue(result['success'])
            self.assertEqual([70, 71, 72, 73, 74], [item['match_score'] for item in result['matches']])
        self.assertEqual(['tailoring', 'interview_help', 'scoring'], [r.operation for r in records])
        self.assertTrue(all(r.provider == 'anthropic' and r.usage.known for r in records))
        self.assertEqual(3, self.opener.open.call_count)
        self.gemini.assert_not_called()
        self.openai.assert_not_called()
        self.assertNotIn('PRIVATE', repr(records))

    def test_headquarters_labels_claude_and_does_not_retry_failure_elsewhere(self):
        with patch.object(maps_providers, 'lookup_headquarters', return_value=maps_providers.HeadquartersResult()):
            self.respond({'address': '123 Synthetic Street, Example, OH', 'country_code': 'US', 'verified': True})
            result = maps_providers.resolve_headquarters(maps_providers.MapsProviderSettings('openstreetmap'),
                                                        self.settings, 'Synthetic Employer')
            self.assertEqual('ai_anthropic', result.source)
            self.assertIn('verify', result.warning)
            self.opener.open.return_value = HTTPResponse(reply('declined', 'refusal'))
            result = maps_providers.resolve_headquarters(maps_providers.MapsProviderSettings('openstreetmap'),
                                                        self.settings, 'Synthetic Employer')
            self.assertEqual('Unknown', result.address)
        self.assertEqual(2, self.opener.open.call_count)
        self.gemini.assert_not_called()
        self.openai.assert_not_called()

    def test_claim_execution_resolves_owner_claude_key_and_restores_context(self):
        # Both manual and scheduled queue entries execute this same claim boundary.
        claim = SearchRunClaim('synthetic-run', 42, 'synthetic-operation', 'Engineer', '', 'lease', 1)
        repository = Mock()
        seen = []
        def secret(provider):
            seen.append((provider_secrets.current_owner_user_id(), provider))
            return 'PRIVATE-CLAUDE'
        def search(keywords, location, checkpoint, **kwargs):
            checkpoint()
            selected = providers.settings_from_profile({'ai_provider': 'anthropic', 'ai_model': claude.MODEL})
            return tailor.analyze_job_match('Synthetic resume', keywords, 'Example', 'Synthetic role',
                        api_key=selected.api_key, ai_provider=selected.provider, ai_model=selected.model)
        self.respond({'match_score': 75, 'match_analysis': 'Synthetic'})
        with provider_secrets.owner_user_scope(7), \
                patch.object(providers, 'provider_secret_store_kind', return_value=providers.SHARED_SECRET_STORE), \
                patch.object(providers, 'current_provider_secret', side_effect=secret), \
                patch.object(execution, 'run_job_search_and_matching', side_effect=search), \
                patch.object(execution.config, 'get_serper_api_key', return_value=''):
            execution.execute_search_claim(claim, repository)
            self.assertEqual(7, provider_secrets.current_owner_user_id())
            repository.succeed.assert_called_once()
            self.assertEqual([(42, 'anthropic')], seen)
            self.opener.open.return_value = HTTPResponse(reply('declined', 'refusal'))
            execution.execute_search_claim(claim, repository)
            self.assertEqual(7, provider_secrets.current_owner_user_id())
            repository.fail.assert_called_once()
        self.assertEqual(2, self.opener.open.call_count)
        self.gemini.assert_not_called()
        self.openai.assert_not_called()

    def test_scanned_pdf_renders_bounded_pngs_and_ocr_keeps_page_order(self):
        with pymupdf.open() as document:
            for index in range(2):
                page = document.new_page(width=5000, height=4000)
                page.draw_rect(pymupdf.Rect(20, 20, 80 + index, 80), color=(0, 0, 0))
            content = document.tobytes()
        self.opener.open.side_effect = [HTTPResponse(reply('Synthetic first page')),
                                       HTTPResponse(reply('Synthetic second page'))]
        with contracts.capture_receipts() as records:
            imported = resume_documents.import_pdf(content, allow_ocr=True,
                ocr_images=lambda images, checkpoint: providers.extract_text_from_images(self.settings, images, checkpoint))
        self.assertTrue(imported.ocr_used)
        self.assertEqual(2, imported.page_count)
        self.assertEqual('Synthetic first page\n\nSynthetic second page', imported.text)
        for call in self.opener.open.call_args_list:
            payload = json.loads(call.args[0].data)
            image = next(item for item in payload['messages'][0]['content'] if item['type'] == 'image')
            raw = base64.b64decode(image['source']['data'])
            self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertLessEqual(len(raw), resume_documents.MAX_OCR_IMAGE_BYTES)
            pixmap = pymupdf.Pixmap(raw)
            self.assertLessEqual(max(pixmap.width, pixmap.height), resume_documents.MAX_OCR_RENDER_DIMENSION)
            self.assertLessEqual(pixmap.width * pixmap.height, resume_documents.MAX_OCR_RENDER_PIXELS)
        self.assertEqual(['resume_ocr', 'resume_ocr'], [r.operation for r in records])
        self.assertTrue(all(r.usage.known for r in records))


if __name__ == '__main__':
    unittest.main()
