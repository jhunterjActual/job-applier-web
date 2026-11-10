"""Supplied bytes are fixtures, never native baseline authentication."""
import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deploy.onedrive_retention import baseline, coordinator

NOW=dt.datetime(2026,10,27,12,tzinfo=dt.timezone.utc)


def evidence():
    pair={'schema':'careertrellis.paired-maintenance-policy/v1','enabled':True,
          'manifest_revision':'b'*40,'application_release':coordinator.RELEASE}
    paired_policy=baseline.canonical(pair)
    policy={'schema':'careertrellis.onedrive-retention-policy/v1','enabled':True,
        'application_release':coordinator.RELEASE,'valid_until':'2027-01-24T00:00:00Z',
        'reserve_percent':20,'peak_multiplier':4,'namespace_budget_bytes':512*1024**2,
        'keep_verified':2,'grace_seconds':172800,'schedule_hour_utc':12,'schedule_minute_utc':30,
        'artifact_revision':'a'*40,'paired_policy_sha256':baseline.digest(paired_policy)}
    standalone_manifest=baseline.canonical({'schema':'careertrellis.onedrive-retention-install/v1',
        'revision':'a'*40,'files':{'entry.py':'e'*64}})
    paired_manifest=baseline.canonical({'schema':'careertrellis.paired-maintenance-install/v1',
        'revision':'b'*40,'files':{'entry.py':'f'*64}})
    policy_raw=baseline.canonical(policy)
    accepted={'schema':baseline.SCHEMA,'standalone_revision':'a'*40,
        'standalone_manifest_sha256':baseline.digest(standalone_manifest),'paired_revision':'b'*40,
        'paired_manifest_sha256':baseline.digest(paired_manifest),'paired_policy_sha256':baseline.digest(paired_policy),
        'application_release':coordinator.RELEASE,'accepted_at':'2026-10-27T11:00:00Z',
        'valid_until':'2027-01-23T00:00:00Z',
        'acceptance_receipts':{'ops':'1'*64,'native_controls':'2'*64,'independent_review':'3'*64}}
    args={'standalone_manifest':standalone_manifest,'paired_manifest':paired_manifest,
        'standalone_policy':policy_raw,'standalone_public_policy':policy_raw,
        'paired_policy':paired_policy,'paired_public_policy':paired_policy,'now':NOW}
    return accepted,args


class BaselineTests(unittest.TestCase):
    def test_pure_byte_validation_is_unauthenticated_even_with_valid_claims(self):
        accepted,args=evidence();result=baseline.validate(accepted,**args)
        self.assertFalse(result.authenticated);self.assertFalse(result.native_actions)
        returned=result.document;returned['application_release']='f'*40
        self.assertEqual(result.document['application_release'],coordinator.RELEASE)
        with self.assertRaises(baseline.BaselineRefused):baseline.AuthenticatedContext(result,'db')
        with self.assertRaises(baseline.BaselineRefused):baseline.AuthenticatedContext(result,'db',object())
        with self.assertRaises(AttributeError):result.authenticated=True
    def test_nonroot_file_bytes_and_owner_claims_remain_explicitly_unauthenticated(self):
        accepted,args=evidence()
        fake=SimpleNamespace(st_uid=1000,raw=baseline.canonical(accepted))
        result=baseline.validate(baseline.decode(fake.raw),**args)
        self.assertFalse(result.authenticated)
        fake.st_uid=0
        self.assertFalse(baseline.validate(baseline.decode(fake.raw),**args).authenticated)
        with self.assertRaises(TypeError):baseline.native_context(root='/caller-chosen')
    def test_revision_manifest_policy_or_public_copy_drift_refuses(self):
        for key in ('standalone_revision','paired_revision','application_release'):
            accepted,args=evidence();accepted[key]='f'*40
            with self.subTest(key=key),self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
        for key in ('standalone_manifest_sha256','paired_manifest_sha256','paired_policy_sha256'):
            accepted,args=evidence();accepted[key]='f'*64
            with self.subTest(key=key),self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
        for key in ('standalone_public_policy','paired_public_policy'):
            accepted,args=evidence();args[key]=b'{}\n'
            with self.subTest(key=key),self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
    def test_future_expired_naive_or_over_policy_cap_refuses(self):
        for key,value in (('accepted_at','2026-10-27T13:00:00Z'),('valid_until','2026-10-27T12:00:00Z'),
                ('valid_until','2027-01-25T00:00:00Z'),('accepted_at','2026-10-27T11:00:00')):
            accepted,args=evidence();accepted[key]=value
            with self.subTest(key=key),self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
        accepted,args=evidence();policy=baseline.decode(args['standalone_policy']);policy['valid_until']='2027-01-01T00:00:00Z'
        args['standalone_policy']=args['standalone_public_policy']=baseline.canonical(policy)
        with self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
    def test_all_receipt_refs_are_required_nonempty_digests_not_paths_or_claim_flags(self):
        for value in ({'ops':'1'*64}, {'ops':'1'*64,'native_controls':'2'*64,'independent_review':'/private/path'},
                      {'ops':'1'*64,'native_controls':'2'*64,'independent_review':'0'*64}):
            accepted,args=evidence();accepted['acceptance_receipts']=value
            with self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
        accepted,args=evidence();accepted['authenticated']=True
        with self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
    def test_disabled_paired_policy_refuses_without_inventing_its_expiry(self):
        accepted,args=evidence();pair=baseline.decode(args['paired_policy']);pair['enabled']=False
        raw=baseline.canonical(pair);args['paired_policy']=args['paired_public_policy']=raw
        accepted['paired_policy_sha256']=baseline.digest(raw)
        policy=baseline.decode(args['standalone_policy']);policy['paired_policy_sha256']=baseline.digest(raw)
        args['standalone_policy']=args['standalone_public_policy']=baseline.canonical(policy)
        with self.assertRaises(baseline.BaselineRefused):baseline.validate(accepted,**args)
        accepted,args=evidence()
        self.assertNotIn('valid_until',baseline.decode(args['paired_policy']))
        self.assertFalse(baseline.validate(accepted,**args).authenticated)
    def test_worktree_or_nonroot_factory_cannot_promote_fake_bytes_to_native(self):
        with patch.object(baseline,'__file__','/untrusted/baseline.py'), \
                patch.object(baseline.coordinator,'Policy') as policy:
            with self.assertRaises(baseline.BaselineRefused):baseline.native_context()
            policy.assert_not_called()
    def test_duplicate_or_noncanonical_json_refuses(self):
        for raw in (b'{"schema":"x","schema":"x"}\n',b'{}',b'{"value":NaN}\n'):
            with self.assertRaises(baseline.BaselineRefused):baseline.decode(raw)
