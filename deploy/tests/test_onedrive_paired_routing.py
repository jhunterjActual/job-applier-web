"""Paired transport extension: bounded requests and read-only status routing."""
import datetime as dt
import unittest
from unittest.mock import patch

from deploy.onedrive_retention import coordinator as c, endpoint
from deploy.paired_maintenance import db_control, entry


def envelope(stage='hydrate'):
    facts = {} if stage in ('hydrate', 'status') else {'predecessor_receipt_sha256':'c'*64}
    inner = {'stage':'hydrate' if stage == 'status' else stage, 'receipt_facts':facts}
    if stage != 'status':
        from deploy.onedrive_retention import guard
        inner['web_history']={'schema':'careertrellis.onedrive-host-history/v1','role':'web',
            'transaction':'a'*32,'stage':stage,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'baseline_bindings':{'standalone_manifest_sha256':'1'*64,'paired_manifest_sha256':'2'*64,
                'paired_policy_sha256':'b'*64,'standalone_policy_sha256':'e'*64},
            'roots':[{'category':category,'root':root,'entry_count':0,'inventory_sha256':'3'*64}
                     for category,root in guard.HISTORY_ROOTS['web']],
            'unresolved':[],'complete':True,'growth_authorized':False,'deletion_authorized':False,
            'authentication':'requires_authenticated_transport',
            'owner':{'pid':123,'start_ticks':456,'process_group':123,
                     'boot_id':'01234567-89ab-cdef-0123-456789abcdef'},
            'reservation':{'path':'/run/lock/careertrellis-maintenance.lock',
                           'identity':{'device':1,'inode':2,'owner_pid':123},'held':True}}
    if stage == 'restore':
        inner['web_preflight'] = {'schema':'careertrellis.onedrive-restore-web-preflight/v1',
            'host':'careertrellis-staging-web-1','authority':'postgres-r2-authoritative',
            'pending_phase':'activated','activation_lease_absent':True,'services_healthy':True,
            'release_binding_verified':True,'release':'d'*40,
            'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    return {'schema':db_control.SCHEMA,'transaction':'a'*32,'policy_sha256':'b'*64,
            'operation':'bundle_'+stage,'payload':{'retention_policy_sha256':'e'*64,
                'capture_transaction':'a'*32,'payload':inner}}


class RoutingTests(unittest.TestCase):
    def test_fixed_preflight_has_no_caller_paths_commands_or_status_alias(self):
        value=envelope()
        value.update(operation=db_control.RETENTION_PREFLIGHT,
                     payload={'stage':'publish','retention_policy_sha256':'e'*64})
        self.assertIs(db_control.validate_request(value),value)
        value['payload']['path']='/etc/anything'
        with self.assertRaises(db_control.Refused):db_control.validate_request(value)

    def test_all_five_operations_validate_in_both_protocol_layers(self):
        for stage in ('hydrate','publish','restore','prepare','retire','status'):
            value = envelope(stage)
            self.assertIs(db_control.validate_request(value), value)
            self.assertEqual(endpoint.from_paired_request(value)['operation'],'bundle_'+stage)

    def test_envelope_rejects_cross_transaction_extra_and_bad_predecessor(self):
        for mutation in ('tx','extra','predecessor'):
            value = envelope('publish')
            if mutation == 'tx':value['payload']['capture_transaction']='f'*32
            elif mutation == 'extra':value['payload']['command']='arbitrary'
            else:value['payload']['payload']['receipt_facts']['predecessor_receipt_sha256']=True
            with self.subTest(mutation=mutation),self.assertRaises(db_control.Refused):
                db_control.validate_request(value)

    def test_release_is_bound_to_selected_policy_not_historical_build96(self):
        value = envelope('restore')['payload']['payload']['web_preflight']
        now = dt.datetime.now(dt.timezone.utc)
        c.web_proof(value,now,'d'*40)
        with self.assertRaises(c.Refused):c.web_proof(value,now,c.RELEASE)

    def test_native_dependency_failure_never_acquires_control_lock(self):
        # Installed retention imports cannot be substituted with an unverified
        # source package in this offline test. Failure still stays read-only.
        from types import SimpleNamespace
        common = SimpleNamespace(canonical=db_control.canonical)
        policy = SimpleNamespace(digest='b'*64,data={})
        modules = {'common':common,'db_control':db_control,
                   'client':SimpleNamespace(decode=lambda raw,maximum:envelope('status'))}
        with patch.object(entry,'_module',side_effect=modules.__getitem__), \
             patch.object(entry,'load_policy',return_value=policy), \
             patch.object(entry.os,'geteuid',return_value=0,create=True), \
             patch.object(entry.socket,'gethostname',return_value=entry.DB_HOST), \
             patch.object(entry,'_control_lock') as lock:
            response=entry.db_endpoint(b'{}')
        self.assertIn(b'operator_required',response)
        lock.assert_not_called()


if __name__ == '__main__':unittest.main()
