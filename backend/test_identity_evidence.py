"""Synthetic manifests only; native tests exercise actual protected-file reads."""
import copy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from admission_operations import encoded
from identity_evidence import EvidenceRejected, submit, submit_protected, validate


def packet(context=None):
    now = datetime.now(timezone.utc)
    context = context or {'operation': str(uuid4()), 'account_id': 2,
        'incarnation': str(uuid4()), 'epoch': 2, 'old_binding': 'a'*64,
        'old_subject': 'user_synthetic_old', 'target_binding': 'b'*64,
        'target_subject': 'user_synthetic_new', 'expected_role': 'user',
        'owner_authorized': False, 'started': (now-timedelta(seconds=5)).isoformat(),
        'deadline': (now+timedelta(seconds=300)).isoformat(), 'predecessor': None}
    timestamp = now.isoformat()
    return {'version': 1, 'context': context,
        'holder': {'method': 'independent_assisted_verification', 'completed_at': timestamp},
        'target': {'method': 'fresh_authentication_and_enrollment', 'completed_at': timestamp, 'factor': 'passkey'},
        'revocation': {'method': 'all_source_and_target_provider_sessions', 'completed_at': timestamp},
        'verified_at': timestamp, 'expires_at': (now+timedelta(seconds=30)).isoformat(),
        'owner_approval': None}


class EvidenceContractTests(unittest.TestCase):
    def test_optional_mfa_v2_methods_and_factor_dispositions(self):
        for method in ('google', 'password', 'passkey'):
            for status in ('none_enrolled', 'enforced'):
                p = packet(); p['version'] = 2
                p['target'] = {'method': 'fresh_authentication_under_optional_mfa',
                    'completed_at': p['verified_at'], 'authentication_method': method,
                    'email_verified': True, 'applicable_factor_status': status}
                if method == 'passkey' and status == 'none_enrolled':
                    with self.assertRaises(EvidenceRejected): validate(encoded(p))
                else:
                    self.assertEqual(p, validate(encoded(p)))

    def test_v2_unverified_or_mixed_evidence_fails_closed(self):
        p = packet(); p['version'] = 2
        p['target'] = {'method': 'fresh_authentication_under_optional_mfa',
            'completed_at': p['verified_at'], 'authentication_method': 'google',
            'email_verified': True, 'applicable_factor_status': 'none_enrolled'}
        for field, value in (('applicable_factor_status', 'unknown'),
                             ('applicable_factor_status', 'bypassed'),
                             ('applicable_factor_status', 'pending'),
                             ('email_verified', False), ('email_verified', 1),
                             ('authentication_method', 'email_match'), ('factor', 'passkey')):
            bad = copy.deepcopy(p); bad['target'][field] = value
            with self.assertRaises(EvidenceRejected): validate(encoded(bad))
        for field in p['target']:
            bad = copy.deepcopy(p); del bad['target'][field]
            with self.assertRaises(EvidenceRejected): validate(encoded(bad))
        bad = copy.deepcopy(p); bad['version'] = 1
        with self.assertRaises(EvidenceRejected): validate(encoded(bad))
        bad = copy.deepcopy(p)
        bad['revocation']['completed_at'] = bad['context']['started']
        with self.assertRaises(EvidenceRejected): validate(encoded(bad))

    def test_valid_outcomes_and_no_raw_evidence_fields(self):
        p = packet()
        self.assertEqual(p, validate(encoded(p)))
        for field in ('email', 'external_id', 'access_token', 'holder_document'):
            bad = copy.deepcopy(p); bad[field] = 'synthetic'
            with self.assertRaises(EvidenceRejected): validate(encoded(bad))

    def test_wrong_types_duplicates_large_and_nonfinite_rejected(self):
        for raw in (b'{}', b'{"version":1,"version":1}', b'{"version":NaN}', b'x'*8193):
            with self.assertRaises(EvidenceRejected): validate(raw)
        for field in ('account_id', 'epoch'):
            p = packet(); p['context'][field] = True
            with self.assertRaises(EvidenceRejected): validate(encoded(p))

    def test_unverified_methods_and_incomplete_factors_rejected(self):
        for section in ('holder', 'target', 'revocation'):
            for method in ('email_match', 'external_id', 'affected_session', 'pending'):
                p = packet(); p[section]['method'] = method
                with self.assertRaises(EvidenceRejected): validate(encoded(p))
        p = packet(); p['target']['factor'] = 'password'
        with self.assertRaises(EvidenceRejected): validate(encoded(p))

    def test_expiry_naive_time_and_old_proof_rejected(self):
        for field, value in (('expires_at', '2000-01-01T00:00:00+00:00'),
                             ('verified_at', '2026-01-01T00:00:00')):
            p = packet(); p[field] = value
            with self.assertRaises(EvidenceRejected): validate(encoded(p))
        p = packet(); p['holder']['completed_at'] = '2000-01-01T00:00:00+00:00'
        with self.assertRaises(EvidenceRejected): validate(encoded(p))

    def test_owner_requires_separate_exact_configuration_authorization(self):
        p = packet(); c = p['context']; c['expected_role'] = 'owner'; c['owner_authorized'] = True
        with self.assertRaises(EvidenceRejected): validate(encoded(p))
        p['owner_approval'] = {k: c[k] for k in ('operation','account_id','epoch','target_binding','target_subject')}
        p['owner_approval'].update(configuration_verified=True, authorized_at=p['verified_at'])
        validate(encoded(p))
        p['owner_approval']['epoch'] += 1
        with self.assertRaises(EvidenceRejected): validate(encoded(p))

    def test_logging_engine_rejected_before_connection_and_errors_sanitized(self):
        engine = Mock(echo=True)
        with self.assertRaisesRegex(EvidenceRejected, '^Operator evidence not acknowledged; inspect operation state.$'):
            submit(encoded(packet()), engine)
        engine.begin.assert_not_called()

    def test_debug_result_logging_rejected_even_with_hidden_parameters(self):
        engine = Mock(echo=False, hide_parameters=True)
        engine.pool.echo = False
        engine.logger.isEnabledFor.return_value = True
        with self.assertRaises(EvidenceRejected): submit(encoded(packet()), engine)
        engine.begin.assert_not_called()


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
    'Native root Linux fixture required')
class ProtectedEvidenceTests(unittest.TestCase):
    def test_untrusted_files_reject_before_database_access(self):
        with tempfile.TemporaryDirectory(dir='/', prefix='ct-evidence-') as directory:
            root = Path(directory); target = root/'packet'; target.write_bytes(encoded(packet()))
            engine = Mock()
            target.chmod(0o644)
            with self.assertRaises(EvidenceRejected): submit_protected(target, engine)
            target.chmod(0o600)
            with patch('identity_evidence.submit', return_value={'outcome':'synthetic'}) as intake:
                self.assertEqual({'outcome':'synthetic'},submit_protected(target,engine))
                intake.assert_called_once_with(target.read_bytes(),engine)
            alias = root/'alias'; alias.symlink_to(target)
            with self.assertRaises(EvidenceRejected): submit_protected(alias, engine)
            alias.unlink(); os.link(target, alias)
            with self.assertRaises(EvidenceRejected): submit_protected(target, engine)
            engine.begin.assert_not_called()
