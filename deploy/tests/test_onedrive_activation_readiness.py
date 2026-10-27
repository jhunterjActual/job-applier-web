"""Pure activation evidence rejection; no provider, marker or timer access."""
import copy
from contextlib import nullcontext
import datetime as dt
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
from deploy.onedrive_retention import activation_readiness as r

NOW=dt.datetime(2026,10,27,16,tzinfo=dt.timezone.utc)
BINDINGS={key:hex(index+1)[2:]*64 for index,key in enumerate(sorted(r.BINDINGS))}


def evidence():
    rows=[]
    for index in range(2):
        rows.append({'archive_sha256':str(index+1)*64,'native_receipt_sha256':str(index+3)*64,
            'terminal_proof_sha256':str(index+5)*64,'backup_label':'2026102%d-155056F'%(index+6),
            'accepted_at':(NOW-dt.timedelta(hours=2-index)).isoformat()})
    return {'schema':r.SCHEMA,'observed_at':NOW.isoformat(),'artifact_revision':'a'*40,
        'application_release':'b'*40,'baseline_bindings':BINDINGS.copy(),'catalog_sha256':'c'*64,
        'replacement_bundles':rows}


class Tests(unittest.TestCase):
    def test_replacement_ages_original_backup_stop_not_restore_completion(self):
        record={'completed_at':NOW.isoformat()}
        for hours,accepted in ((0,True),(36,True),(36.01,False),(-1,False)):
            with self.subTest(hours=hours), patch.object(r.native_restore,
                    'accepted_backup_completed_at',return_value=(NOW-dt.timedelta(hours=hours)).isoformat()) as read:
                if accepted:r.recovery_point(record,NOW)
                else:
                    with self.assertRaisesRegex(r.ReadinessRefused,'replacement_recovery_point_stale'):
                        r.recovery_point(record,NOW)
                read.assert_called_once_with(record)

    def test_missing_original_info_metadata_never_backfilled(self):
        with patch.object(r.native_restore,'accepted_backup_completed_at',side_effect=FileNotFoundError), \
             self.assertRaises(FileNotFoundError):
            r.recovery_point({},NOW)

    def test_current_backup_fixed_query_latest_and_existing_lock(self):
        from deploy.onedrive_retention import hydrate,builder
        import json
        class Context:
            authenticated=True;role='db';bindings=BINDINGS;revision='a'*40
        now=dt.datetime.now(dt.timezone.utc)
        rows=[{'label':'20261026-155056F','timestamp':{'stop':int(now.timestamp())-7200},'info':{'size':123}},
              {'label':'20261026-155056F_20261027-155056D','timestamp':{'stop':int(now.timestamp())-60},'info':{'size':234}}]
        info=[{'name':'careertrellis','status':{'code':0},'backup':rows}]
        path=Mock();path.absolute.return_value=path
        files=Mock()
        with patch.object(r,'Path',return_value=path),patch.object(r.os,'name','posix'),\
             patch.object(r.os,'geteuid',return_value=0,create=True),patch.object(r.os,'getegid',return_value=0,create=True),\
             patch.object(r.socket,'gethostname',return_value='job-applier-db-staging-01'),\
             patch.object(r.baseline,'AuthenticatedContext',Context),patch.object(r.baseline,'native_context',return_value=Context()),\
             patch.object(r.catalog,'NativeFiles',return_value=files) as factory,\
             patch.object(hydrate,'command',return_value=json.dumps(info).encode()) as command:
            result=r.native_current_backup(27)
            self.assertEqual(result['backup_label'],rows[1]['label'])
            self.assertEqual(result['backup_completed_at'],dt.datetime.fromtimestamp(rows[1]['timestamp']['stop'],dt.timezone.utc).isoformat())
            factory.assert_called_once_with(27);self.assertEqual(files.require_repository_lock.call_count,2)
            command.assert_called_once_with(['/usr/bin/setpriv','--reuid=postgres','--regid=postgres','--init-groups',
                '--reset-env','--','/usr/bin/pgbackrest','--stanza=careertrellis','--output=json','info'],
                output=True,lock_descriptor=27,timeout=120)
            self.assertEqual(set(result),{'schema','backup_label','backup_completed_at','observed_at'})
            rows[1]['error']=True
            command.return_value=json.dumps(info).encode()
            with self.assertRaises(builder.BuilderRefused):r.native_current_backup(27)
            command.reset_mock();files.require_repository_lock.side_effect=ValueError('lost fd')
            with self.assertRaises(ValueError):r.native_current_backup(27)
            command.assert_not_called()

    def test_peer_state_missing_actor_publication_never_collects_or_locks(self):
        from deploy.onedrive_retention import entry,activation,activation_transition,health
        class Context:
            authenticated=True;role='db';bindings=BINDINGS;revision='a'*40
            document={'application_release':'b'*40,'acceptance_receipts':{'ops':'f'*64}}
        tx='d'*32;intent={'transaction':tx,'valid_until':'2027-01-23T00:00:00Z',
            'installation_receipts':{'web':'e'*64,'db':'f'*64}}
        fresh=evidence();fresh['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        intent['replacement_bundles']=fresh['replacement_bundles']
        sha=r.catalog.sha(r.bundle.canonical(intent))
        review={'prepare_deadline':'2026-10-28T00:00:00Z','transaction':tx,'role':'db',
            'baseline_sha256':r.catalog.sha(r.bundle.canonical(Context.document)),
            'baseline_bindings':BINDINGS,'intent_sha256':sha,
            'installation_receipts':intent['installation_receipts']}
        request={'transaction':tx,'role':'db','phase':'prepare','intent_sha256':sha,
            'prepare_deadline':review['prepare_deadline'],'baseline_bindings':BINDINGS}
        prepared={'transaction':tx,'role':'db','phase':'prepare','intent_sha256':sha,
            'request_sha256':r.catalog.sha(r.bundle.canonical(request)),'result':'prepared'}
        records={activation.INTENT:intent,activation_transition.REVIEW_ROOT/tx/'review.json':review,
            activation_transition.REVIEW_ROOT/tx/'prepare-request-db.json':request,
            activation_transition.REVIEW_ROOT/tx/'prepared-db.json':prepared}
        def read(path,*args):
            if path not in records:raise FileNotFoundError(2,'missing actor publication')
            value=records[path];return r.bundle.canonical(value),value
        with patch.object(r.baseline,'AuthenticatedContext',Context),patch.object(entry,'verify_install'),\
             patch.object(entry,'_native_dependencies',return_value=Context()),\
             patch.object(entry,'_protected_document',side_effect=read),\
             patch.object(type(activation.SELECTOR),'lstat',side_effect=FileNotFoundError(2,'absent')),\
             patch.object(activation,'validate'),patch.object(r,'native_replacements') as collect,\
             patch.object(health,'_installation') as installation:
            with self.assertRaises(FileNotFoundError):r.native_activation_state(tx,sha)
            collect.assert_not_called();installation.assert_not_called()
            packet={'schema':'careertrellis.retention-activation-peer-observation/v1',
                'transaction':tx,'phase':'prepare','observed_at':fresh['observed_at'],
                'baseline_sha256':review['baseline_sha256'],'baseline_bindings':BINDINGS,
                'installation_receipt_sha256':'f'*64,'intent_sha256':sha,
                'prepared_receipt_sha256':r.catalog.sha(r.bundle.canonical(prepared)),
                'review_sha256':r.catalog.sha(r.bundle.canonical(review)),'replacement_evidence':fresh}
            path=activation_transition.REVIEW_ROOT/tx/'peer-state-prepare.json'
            records[path]=packet;installation.return_value=(None,'f'*64)
            answer=r.native_activation_state(tx,sha)
            self.assertTrue(answer['prepared']);self.assertIsNone(answer['selector'])
            for key in ('transaction','intent_sha256','prepared_receipt_sha256','baseline_sha256',
                        'installation_receipt_sha256','review_sha256'):
                original=packet[key];packet[key]='0'*len(original)
                with self.subTest(key=key),self.assertRaises(ValueError):r.native_activation_state(tx,sha)
                packet[key]=original
            packet['observed_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=121)).isoformat()
            with self.assertRaises(ValueError):r.native_activation_state(tx,sha)
            collect.assert_not_called()

    def test_historical_restore_must_bind_this_policy_and_application_release(self):
        proof={'schema':'careertrellis.onedrive-restore-web-preflight/v1','host':'careertrellis-staging-web-1',
            'authority':'postgres-r2-authoritative','pending_phase':'activated','activation_lease_absent':True,
            'services_healthy':True,'release_binding_verified':True,'release':'b'*40,
            'observed_at':(NOW-dt.timedelta(hours=6)).isoformat()}
        request={'schema':r.endpoint.SCHEMA,'operation':'bundle_restore','retention_policy_sha256':'c'*64,
            'capture_transaction':'d'*32,'payload':{'stage':'restore','receipt_facts':{},
                'web_preflight':proof,'web_history':{}}}
        self.assertEqual(r.restore_binding(request,'d'*32,'c'*64,'b'*40),proof)
        for tx,policy,release in (('e'*32,'c'*64,'b'*40),('d'*32,'e'*64,'b'*40),('d'*32,'c'*64,'e'*40)):
            with self.assertRaises(ValueError):r.restore_binding(request,tx,policy,release)

    def test_claims_produce_only_explicit_unauthenticated_review_bytes(self):
        result=r.intent_candidate(evidence(),transaction='d'*32,bindings=BINDINGS,revision='a'*40,
            release='b'*40,installation_receipts={'web':'e'*64,'db':'f'*64},
            reviews={key:'1'*64 for key in r.REVIEWS},now=NOW,valid_until='2027-01-23T00:00:00Z')
        self.assertFalse(result['authenticated']);self.assertFalse(result['activation_performed'])
        self.assertEqual(len(result['intent']['replacement_bundles']),2)
        result['intent']['baseline_bindings'].clear()
        self.assertEqual(len(BINDINGS),4)

    def test_missing_second_or_duplicate_archive_native_or_terminal_proof_refuses(self):
        for field in ('archive_sha256','native_receipt_sha256','terminal_proof_sha256',None):
            value=evidence()
            if field:value['replacement_bundles'][1][field]=value['replacement_bundles'][0][field]
            else:value['replacement_bundles'].pop()
            with self.subTest(field=field),self.assertRaises(r.ReadinessRefused):
                r.validate_evidence(value,BINDINGS,'a'*40,'b'*40,NOW)

    def test_stale_future_wrong_binding_unknown_field_or_reversed_order_refuses(self):
        mutations=(lambda x:x.update(observed_at=(NOW-dt.timedelta(seconds=121)).isoformat()),
            lambda x:x.update(observed_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda x:x['baseline_bindings'].update(standalone_policy_sha256='0'*64),
            lambda x:x.update(authenticated=True),
            lambda x:x['replacement_bundles'].reverse(),
            lambda x:x['replacement_bundles'][0].update(accepted_at=(NOW+dt.timedelta(seconds=1)).isoformat()))
        for mutate in mutations:
            value=copy.deepcopy(evidence());mutate(value)
            with self.assertRaises(r.ReadinessRefused):r.validate_evidence(value,BINDINGS,'a'*40,'b'*40,NOW)

    def test_missing_review_or_unbounded_expiry_cannot_form_candidate(self):
        args=dict(transaction='d'*32,bindings=BINDINGS,revision='a'*40,release='b'*40,
            installation_receipts={'web':'e'*64,'db':'f'*64},reviews={key:'1'*64 for key in r.REVIEWS},
            now=NOW,valid_until='2027-01-23T00:00:00Z')
        for changes in ({'reviews':{}},{'valid_until':NOW.isoformat()},
                        {'valid_until':'2099-01-01T00:00:00Z'},{'transaction':'0'*32}):
            with self.subTest(changes=changes),self.assertRaises(r.ReadinessRefused):
                r.intent_candidate(evidence(),**dict(args,**changes))

    def test_fixture_context_cannot_reach_provider_inventory_or_catalog(self):
        fake=SimpleNamespace(authenticated=True,role='db')
        with patch.object(r.baseline,'native_context',return_value=fake), \
             patch.object(r.guard,'native_catalog_inventory') as inventory, \
             patch.object(r.catalog,'NativeReadStore') as store,self.assertRaises(r.ReadinessRefused):
            r.native_replacements(9)
        inventory.assert_not_called();store.assert_not_called()

    def test_missing_provider_object_refuses_before_recovery_journal_reads(self):
        # Injected test-only context does not exercise the installed factory.
        fixture=r.baseline.ValidatedFixture({'standalone_revision':'a'*40,
            'application_release':'b'*40},BINDINGS)
        context=r.baseline.AuthenticatedContext(fixture,'db',r.baseline._NATIVE_SEAL)
        store=Mock();store.snapshot.side_effect=r.catalog.CatalogRefused('missing_or_unregistered_objects_require_reconciliation')
        store.io.locked.return_value=nullcontext();store.records.return_value={'a':{},'b':{}}
        store.retired.return_value=set()
        observed={'authenticated':True,'inventory':{'objects':[]},'catalog':{'entries':[]}}
        with patch.object(r.baseline,'native_context',return_value=context), \
             patch.object(r.guard,'native_catalog_inventory',return_value=observed), \
             patch.object(r.catalog,'NativeReadStore',return_value=store),self.assertRaises(r.catalog.CatalogRefused):
            r.native_replacements(9)
        store.io.read.assert_not_called();store.snapshot.assert_called_once_with(observed['inventory'])


class BootstrapWebCollectionTests(unittest.TestCase):
    def fixture(self):
        now=dt.datetime.now(dt.timezone.utc);proof=evidence()
        proof['observed_at']=now.isoformat()
        for index,row in enumerate(proof['replacement_bundles']):
            row['accepted_at']=(now-dt.timedelta(hours=2-index)).isoformat()
        context=SimpleNamespace(bindings=BINDINGS.copy(),revision='a'*40,
            document={'application_release':'b'*40,'acceptance_receipts':{'ops':'7'*64}})
        value={'schema':r.BOOTSTRAP_SCHEMA,'transaction':'d'*32,
            'retention_policy_sha256':BINDINGS['standalone_policy_sha256'],
            'db_install_receipt_sha256':'8'*64,'db_baseline_sha256':'9'*64,'replacement_evidence':proof}
        return context,value

    def test_default_source_factory_refuses_before_network_or_marker_changes(self):
        with patch.object(r,'_bootstrap_client') as client,self.assertRaises(r.ReadinessRefused):
            r.collect_bootstrap_evidence('d'*32)
        client.assert_not_called()

    def test_caller_dictionary_cannot_construct_authenticated_bootstrap_observations(self):
        context,value=self.fixture()
        with self.assertRaises(r.ReadinessRefused):r.AuthenticatedBootstrapEvidence(value,context.bindings)

    def test_actual_fixed_client_result_is_bound_and_returned_as_readonly_sealed_observations(self):
        from deploy.onedrive_retention import activation
        context,value=self.fixture();client=Mock();client.call.return_value=value
        with patch.object(r,'_bootstrap_web_context',return_value=context), \
                patch.object(r,'_bootstrap_client',return_value=client), \
                patch.object(activation,'routing_state',return_value=None) as state:
            result=r.collect_bootstrap_evidence('d'*32)
        self.assertIs(type(result),r.AuthenticatedBootstrapEvidence)
        self.assertTrue(result.authenticated);self.assertEqual(result.role,'web')
        self.assertEqual(result.value,value);self.assertEqual(result.bindings,context.bindings)
        self.assertEqual(result.digest,r.catalog.sha(r.bundle.canonical(value)))
        self.assertEqual(state.call_count,2)
        client.call.assert_called_once_with('retention_bootstrap_evidence',{
            'retention_policy_sha256':BINDINGS['standalone_policy_sha256']})
        changed=result.value;changed['db_install_receipt_sha256']='0'*64
        self.assertEqual(result.value['db_install_receipt_sha256'],'8'*64)

    def test_active_or_partial_web_state_prevents_collection(self):
        from deploy.onedrive_retention import activation
        for mode in ('active','partial'):
            context,value=self.fixture()
            state=Mock(return_value=object())
            if mode=='partial':state.side_effect=activation.ActivationRefused()
            with patch.object(r,'_bootstrap_web_context',return_value=context), \
                    patch.object(r,'_bootstrap_client') as client, \
                    patch.object(activation,'routing_state',state),self.assertRaises(Exception):
                r.collect_bootstrap_evidence('d'*32)
            client.assert_not_called()

    def test_wrong_transaction_policy_or_missing_second_acceptance_never_authenticates(self):
        from deploy.onedrive_retention import activation
        for change in (lambda v:v.update(transaction='e'*32),
            lambda v:v.update(retention_policy_sha256='e'*64),
            lambda v:v.update(db_install_receipt_sha256='0'*64),
            lambda v:v['replacement_evidence']['replacement_bundles'].pop(),
            lambda v:v['replacement_evidence'].update(observed_at='2020-01-01T00:00:00Z')):
            context,value=self.fixture();change(value);client=Mock();client.call.return_value=value
            with patch.object(r,'_bootstrap_web_context',return_value=context), \
                    patch.object(r,'_bootstrap_client',return_value=client), \
                    patch.object(activation,'routing_state',return_value=None),self.assertRaises(r.ReadinessRefused):
                r.collect_bootstrap_evidence('d'*32)

    def test_marker_appearance_or_native_factory_failure_is_not_bootstrap_authority(self):
        from deploy.onedrive_retention import activation
        for fail in (False,True):
            context,value=self.fixture();client=Mock();client.call.return_value=value
            if fail:client.call.side_effect=RuntimeError('native proof unavailable')
            with patch.object(r,'_bootstrap_web_context',return_value=context), \
                    patch.object(r,'_bootstrap_client',return_value=client), \
                    patch.object(activation,'routing_state',side_effect=[None,object()]),self.assertRaises(Exception):
                r.collect_bootstrap_evidence('d'*32)
