"""Paired transport extension: bounded requests and read-only status routing."""
import datetime as dt
import copy
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import hashlib
import sys
import types

from deploy.onedrive_retention import coordinator as c, endpoint
from deploy.paired_maintenance import db_control, entry


class FinalizationRPCTests(unittest.TestCase):
    def fixture(self):
        from deploy.onedrive_retention import activation_finalize as finalizer,bundle
        from deploy.tests.test_onedrive_activation_finalize import material,acknowledgement,NOW
        value,_web,context=material();context.authenticated=True
        request={'schema':db_control.SCHEMA,'transaction':value['transaction'],
            'policy_sha256':context.bindings['paired_policy_sha256'],
            'operation':db_control.RETENTION_FINALIZE,'payload':value}
        policy=SimpleNamespace(digest=context.bindings['standalone_policy_sha256'],
            data={'application_release':context.document['application_release'],
                  'paired_policy_sha256':context.bindings['paired_policy_sha256']})
        receive=Mock(return_value=acknowledgement(value))
        native=SimpleNamespace(receive=receive,validate_ack=Mock(side_effect=lambda ack,value,now:
            finalizer.validate_ack(ack,value,NOW+dt.timedelta(minutes=8))))
        loader=SimpleNamespace(_load=Mock(return_value=native),canonical=bundle.canonical,
            verify_install=Mock(return_value=context.revision),_native_dependencies=Mock(return_value=context))
        return loader,request,policy,context,native

    def test_closed_wire_claims_and_exact_chain_bindings(self):
        loader,request,policy,context,native=self.fixture()
        self.assertIs(db_control.validate_request(request),request)
        self.assertLessEqual(len(loader.canonical(request)),16384)
        mutations=(lambda r:r['payload'].update(extra=True),
            lambda r:r['payload'].update(transaction='a'*32),
            lambda r:r['payload']['documents'].pop('reconciliation-db.json'),
            lambda r:r['payload']['documents']['commit-ack-db.json'].update(transaction='a'*32),
            lambda r:r['payload']['selector'].update(activation_intent_sha256='9'*64),
            lambda r:r['payload']['finalized'].update(controller_terminal_sha256='9'*64),
            lambda r:r['payload']['intent']['baseline_bindings'].update(paired_policy_sha256='9'*64),
            lambda r:r['payload']['intent'].update(application_release='x'*17000))
        for mutate in mutations:
            changed=copy.deepcopy(request);mutate(changed)
            with self.subTest(change=mutate),patch.object(entry,'_module',return_value=db_control),self.assertRaises(Exception):
                entry._retention_activation_finalize(loader,changed,policy,context)
            loader._load.assert_not_called();native.receive.assert_not_called()

    def test_fixed_receiver_gets_exact_payload_once_without_outer_fences(self):
        loader,request,policy,context,native=self.fixture()
        with patch.object(entry,'_module',return_value=db_control),patch.object(entry,'_control_lock') as control,\
             patch.object(entry,'_transaction_store') as store:
            result=entry._retention_activation_finalize(loader,request,policy,context)
        loader._load.assert_called_once_with('activation_finalize')
        native.receive.assert_called_once_with(loader.canonical(request['payload']))
        native.validate_ack.assert_called_once()
        self.assertIs(result,native.receive.return_value)
        control.assert_not_called();store.assert_not_called()

    def test_endpoint_routes_finalization_without_outer_lock_or_store(self):
        loader,request,policy,context,native=self.fixture()
        finalizer_load=loader._load
        coordinator=SimpleNamespace(Policy=lambda value:policy,PRIVATE_POLICY=object())
        loader._load=lambda name:coordinator if name=='coordinator' else (SimpleNamespace() if name=='endpoint' else finalizer_load(name))
        loader._protected_document=lambda *args:(b'',policy.data)
        package=types.ModuleType('onedrive_retention');package.entry=loader
        modules={'common':SimpleNamespace(canonical=loader.canonical),'db_control':db_control,
            'client':SimpleNamespace(decode=lambda raw,maximum:request)}
        paired=SimpleNamespace(digest=request['policy_sha256'],data={'application_release':context.document['application_release']})
        with patch.object(entry,'_module',side_effect=modules.__getitem__),patch.object(entry,'load_policy',return_value=paired),\
             patch.object(entry.os,'geteuid',return_value=0,create=True),\
             patch.object(entry.socket,'gethostname',return_value=entry.DB_HOST),\
             patch.object(entry,'_control_lock') as lock,patch.object(entry,'_transaction_store') as store,\
             patch.dict(sys.modules,{'onedrive_retention':package}):
            raw=entry.db_endpoint(loader.canonical(request))
        self.assertIn(b'actor_terminal_sha256',raw)
        native.receive.assert_called_once_with(loader.canonical(request['payload']))
        lock.assert_not_called();store.assert_not_called()

    def test_unallowlisted_module_refuses_before_native_receive(self):
        from deploy.onedrive_retention import entry as fixed
        loader,request,policy,context,native=self.fixture();loader._load=fixed._load
        with patch.object(entry,'_module',return_value=db_control),\
             patch.object(fixed,'FILES',set(fixed.FILES)-{'activation_finalize.py'}),\
             patch.object(fixed,'verify_install') as verify:
            with self.assertRaises(fixed.EntryRefused):entry._retention_activation_finalize(loader,request,policy,context)
            verify.assert_not_called()
        native.receive.assert_not_called()

    def test_current_context_or_returned_ack_drift_is_not_success(self):
        loader,request,policy,context,native=self.fixture()
        context.revision='9'*40
        with patch.object(entry,'_module',return_value=db_control),self.assertRaises(entry.EntryRefused):
            entry._retention_activation_finalize(loader,request,policy,context)
        native.receive.assert_not_called()
        loader,request,policy,context,native=self.fixture()
        native.receive.return_value['selector_sha256']='9'*64
        with patch.object(entry,'_module',return_value=db_control),self.assertRaises(ValueError):
            entry._retention_activation_finalize(loader,request,policy,context)
        native.receive.assert_called_once()


class ActivationBridgeTests(unittest.TestCase):
    def fixture(self,operation):
        from deploy.onedrive_retention import bundle
        bindings={'standalone_manifest_sha256':'1'*64,'paired_manifest_sha256':'2'*64,
            'paired_policy_sha256':'b'*64,'standalone_policy_sha256':'e'*64}
        request={'schema':db_control.SCHEMA,'transaction':'a'*32,'policy_sha256':'b'*64,'operation':operation,
            'payload':{'baseline_bindings':bindings,'installation_receipts':{'web':'3'*64,'db':'f'*64},
                'intent_sha256':'c'*64,'web_baseline_sha256':'4'*64}}
        if operation==db_control.RETENTION_COMMIT:request['payload']['commit_authority_sha256']='5'*64
        if operation in (db_control.RETENTION_ACTOR_STATE,db_control.RETENTION_ACTOR_TERMINAL_STATUS):
            request['payload']={'retention_policy_sha256':'e'*64,'intent_sha256':'c'*64}
        owner={'pid':123,'start_ticks':456,'boot_id':'11111111-1111-1111-1111-111111111111'}
        if operation==db_control.RETENTION_MATERIAL:
            deadline='2026-10-28T00:10:00Z'
            prepared={'transaction':'a'*32,'role':'web','phase':'prepare','intent_sha256':'c'*64,
                'prepare_deadline':deadline,'baseline_bindings':bindings}
            receipt={'transaction':'a'*32,'role':'web','phase':'prepare','intent_sha256':'c'*64,
                'request_sha256':hashlib.sha256(bundle.canonical(prepared)).hexdigest(),'result':'prepared'}
            selector={'schema':'careertrellis.onedrive-retention-activation/v1','transaction':'a'*32,
                'activation_intent_sha256':'c'*64,'enabled':True,'activated_at':'2026-10-28T00:01:00Z',
                'valid_until':'2027-01-01T00:00:00Z'}
            authority={'schema':'careertrellis.retention-activation-commit-authority/v1','transaction':'a'*32,
                'intent_sha256':'c'*64,'prepare_deadline':deadline,'dispatcher_owner':owner,
                'prepared':{'web':hashlib.sha256(bundle.canonical(receipt)).hexdigest(),'db':'9'*64},
                'selector':selector,'selector_sha256':hashlib.sha256(bundle.canonical(selector)).hexdigest()}
            request['payload']={'transaction':'a'*32,'retention_policy_sha256':'e'*64,'intent_sha256':'c'*64,
                'documents':{'commit-authority.json':authority,'prepared-web.json':receipt,'prepare-request-web.json':prepared}}
        context=SimpleNamespace(role='db',authenticated=True,bindings=bindings,document={
            'application_release':'d'*40,'acceptance_receipts':{'ops':'f'*64}})
        state={'schema':'careertrellis.retention-activation-actor-state/v1','transaction':'a'*32,
            'intent_sha256':'c'*64,'actor_owner':owner,'actor_invocation':'6'*32,'prepare_request_sha256':'9'*64,
            'baseline_bindings':bindings,'baseline_sha256':hashlib.sha256(bundle.canonical(context.document)).hexdigest(),
            'source':{'paired_entry_sha256':'7'*64,'actor_sha256':'8'*64}}
        receipt={'transaction':'a'*32,'role':'db','phase':'prepare','intent_sha256':'c'*64,
            'request_sha256':'9'*64,'result':'prepared'}
        if operation==db_control.RETENTION_COMMIT:
            receipt={'transaction':'a'*32,'role':'db','phase':'commit','intent_sha256':'c'*64,
                'selector_sha256':'9'*64,'committed_at':'2026-10-28T00:02:00Z','result':'committed'}
        material={'schema':'careertrellis.retention-activation-material-accepted/v1','transaction':'a'*32,
            'material_sha256':'9'*64,'value_sha256':hashlib.sha256(bundle.canonical(request['payload'])).hexdigest(),
            'actor_owner':owner,'actor_invocation':'6'*32}
        actor=SimpleNamespace(submit=Mock(return_value=receipt),observe=Mock(return_value=state),
            submit_material=Mock(return_value=material))
        terminal_state={key:value for key,value in state.items() if key!='prepare_request_sha256'}
        terminal_state.update(schema='careertrellis.retention-activation-actor-terminal-status/v1',
            observed_at=dt.datetime.now(dt.timezone.utc).isoformat(),state='running',terminal=None)
        actor.terminal_status=Mock(return_value=terminal_state)
        loader=SimpleNamespace(_load=Mock(return_value=actor),canonical=bundle.canonical,
            verify_install=Mock(return_value='a'*40),_native_dependencies=Mock(return_value=context))
        policy=SimpleNamespace(digest='e'*64,data={'application_release':'d'*40,'paired_policy_sha256':'b'*64})
        return request,context,actor,loader,policy

    def test_closed_bridge_requests_reject_zero_cross_policy_and_extra_claims(self):
        for operation in db_control.RETENTION_BRIDGE:
            request,*unused=self.fixture(operation)
            self.assertIs(db_control.validate_request(request),request)
            for mutation in ('tx','policy','extra'):
                broken=copy.deepcopy(request)
                if mutation=='tx':broken['transaction']='0'*32
                elif mutation=='policy':broken['policy_sha256']='0'*64
                else:broken['payload']['command']='untrusted'
                with self.subTest(operation=operation,mutation=mutation),self.assertRaises(Exception):
                    db_control.validate_request(broken)

    def test_material_document_names_owner_receipt_and_selector_are_bound(self):
        request,*unused=self.fixture(db_control.RETENTION_MATERIAL)
        for mutation in ('path','owner','selector','receipt','policy','oversize'):
            broken=copy.deepcopy(request);docs=broken['payload']['documents']
            if mutation=='path':docs['other.json']={}
            elif mutation=='owner':docs['commit-authority.json']['dispatcher_owner']['pid']=True
            elif mutation=='selector':docs['commit-authority.json']['selector']['enabled']=False
            elif mutation=='receipt':docs['prepared-web.json']['request_sha256']='0'*64
            elif mutation=='policy':docs['prepare-request-web.json']['baseline_bindings']['standalone_policy_sha256']='1'*64
            else:docs['commit-authority.json']['prepare_deadline']='x'*17000
            with self.subTest(mutation=mutation),self.assertRaises(Exception):db_control.validate_request(broken)

    def test_bridge_routes_only_exact_fixed_actor_methods_without_other_locks(self):
        for operation in db_control.RETENTION_BRIDGE:
            request,context,actor,loader,policy=self.fixture(operation)
            with patch.object(entry,'_module',return_value=db_control),patch.object(entry,'_control_lock') as control,\
                    patch.object(entry,'_transaction_store') as transaction:
                result=entry._retention_activation_bridge(loader,request,policy,context)
            control.assert_not_called();transaction.assert_not_called();loader._load.assert_called_once_with('activation_actor')
            if operation==db_control.RETENTION_ACTOR_STATE:
                actor.observe.assert_called_once_with('a'*32,'c'*64);actor.submit.assert_not_called();actor.submit_material.assert_not_called()
            elif operation==db_control.RETENTION_ACTOR_TERMINAL_STATUS:
                actor.terminal_status.assert_called_once_with('a'*32,'c'*64)
                actor.submit.assert_not_called();actor.observe.assert_not_called();actor.submit_material.assert_not_called()
            elif operation==db_control.RETENTION_MATERIAL:
                actor.submit_material.assert_called_once_with(loader.canonical(request['payload']));actor.submit.assert_not_called();actor.observe.assert_not_called()
            else:
                actor.submit.assert_called_once_with(loader.canonical(request));actor.observe.assert_not_called();actor.submit_material.assert_not_called()
            self.assertEqual(result['transaction'],'a'*32)

    def test_wrong_current_binding_refuses_before_actor_load_or_dispatch(self):
        request,context,actor,loader,policy=self.fixture(db_control.RETENTION_PREPARE)
        request['payload']['installation_receipts']['db']='1'*64
        with patch.object(entry,'_module',return_value=db_control),self.assertRaises(Exception):
            entry._retention_activation_bridge(loader,request,policy,context)
        loader._load.assert_not_called();actor.submit.assert_not_called()

    def test_actor_outside_installed_allowlist_cannot_be_loaded(self):
        from deploy.onedrive_retention import entry as retention_entry
        self.assertIn('activation_actor.py',retention_entry.FILES)
        with patch.object(retention_entry,'FILES',retention_entry.FILES-{'activation_actor.py'}),\
             patch.object(retention_entry,'verify_install') as verify:
            with self.assertRaises(retention_entry.EntryRefused):retention_entry._load('activation_actor')
            verify.assert_not_called()

    def test_full_endpoint_observer_does_not_take_transaction_or_repository_lock(self):
        from deploy.paired_maintenance import common
        request,context,actor,loader,policy=self.fixture(db_control.RETENTION_ACTOR_STATE)
        operations=SimpleNamespace(Endpoint=Mock(),NativeDB=Mock())
        coordinator=SimpleNamespace(PRIVATE_POLICY=object(),Policy=Mock(return_value=policy))
        loader._protected_document=Mock(return_value=(b'{}',{}))
        loader._load.side_effect=lambda name:{'activation_actor':actor,'endpoint':operations,'coordinator':coordinator}[name]
        package=types.ModuleType('onedrive_retention');package.entry=loader
        modules={'common':common,'db_control':db_control,'client':SimpleNamespace(decode=lambda raw,maximum:request)}
        paired=SimpleNamespace(digest='b'*64,data={'application_release':'d'*40})
        with patch.dict(sys.modules,{'onedrive_retention':package}),patch.object(entry,'_module',side_effect=modules.__getitem__),\
                patch.object(entry,'load_policy',return_value=paired),patch.object(entry.os,'geteuid',return_value=0,create=True),\
                patch.object(entry.socket,'gethostname',return_value=entry.DB_HOST),patch.object(entry,'_control_lock') as control,\
                patch.object(entry,'_transaction_store') as transaction:
            answer=entry.db_endpoint(b'{}')
        self.assertNotIn(b'operator_required',answer);actor.observe.assert_called_once()
        actor.submit.assert_not_called();actor.submit_material.assert_not_called()
        control.assert_not_called();transaction.assert_not_called();operations.Endpoint.assert_not_called()


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
    def test_activation_state_exact_read_only_request(self):
        value=envelope();value.update(operation=db_control.RETENTION_STATE,
            payload={'retention_policy_sha256':'e'*64,'intent_sha256':'f'*64})
        self.assertEqual(db_control.validate_request(value),value)
        for extra in ('path','replacement_evidence','prepared','selector'):
            changed=copy.deepcopy(value);changed['payload'][extra]=True
            with self.assertRaises(Exception):db_control.validate_request(changed)
        for key in value['payload']:
            changed=copy.deepcopy(value);changed['payload'][key]='0'*64
            with self.assertRaises(Exception):db_control.validate_request(changed)
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


class ReviewedHealthPinsTests(unittest.TestCase):
    def test_installed_health_pins_match_reviewed_public_bytes(self):
        import hashlib
        from pathlib import Path
        from deploy.onedrive_retention import hydrate
        from deploy.paired_maintenance import runtime, db_host
        root = Path(__file__).resolve().parents[2]
        core = hashlib.sha256((root/'backend/backup_health.py').read_bytes()).hexdigest()
        wrapper = hashlib.sha256((root/'deploy/scripts/manage-backup-health.sh').read_bytes()).hexdigest()
        web_wrapper = hashlib.sha256((root/'deploy/scripts/manage-generated-object-backup-health.sh').read_bytes()).hexdigest()
        self.assertEqual(hydrate.PINS['backup_health'], core)
        for role in ('web', 'db'):
            self.assertEqual(runtime._BASE_PINS[role][runtime.CORE], core)
            self.assertEqual(runtime._BASE_PINS[role]['/usr/local/sbin/careertrellis-backup-health'],
                             web_wrapper if role == 'web' else wrapper)
        self.assertNotEqual(web_wrapper, wrapper)
        self.assertEqual(db_host.HEALTH_PINS['/usr/local/sbin/careertrellis-backup-health'], wrapper)
        self.assertEqual(runtime._BASE_PINS['web'][runtime.HELPER],
            hashlib.sha256((root/'deploy/scripts/mirror-generated-objects-onedrive.sh').read_bytes()).hexdigest())
        self.assertEqual(db_host.MIRROR_PINS['/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive'],
            hashlib.sha256((root/'deploy/scripts/mirror-pgbackrest-onedrive.sh').read_bytes()).hexdigest())
        self.assertEqual(runtime._BASE_PINS['db']['/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive'],
                         db_host.MIRROR_PINS['/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive'])

    def test_policy_current_health_paths_are_allowed_but_unknown_paths_refuse(self):
        from types import SimpleNamespace
        from deploy.paired_maintenance import runtime
        data = {'application_release':'a'*40,'ops_release':'b'*40,'application_build':'test',
                'max_capture_objects':1,'max_capture_bytes':1,
                'artifact_hashes':{role:{runtime.CORE:'c'*64,
                    '/usr/local/sbin/careertrellis-backup-health':'d'*64} for role in ('web','db')}}
        saved = {name:getattr(runtime,name) for name in
                 ('RELEASE','OPS','BUILD','BASE','PINS','MAX_CAPTURE_OBJECTS','MAX_CAPTURE_BYTES')}
        try:
            runtime.configure(SimpleNamespace(data=data), 'db')
            self.assertEqual(runtime.PINS['web'][runtime.CORE], 'c'*64)
            self.assertEqual(runtime.PINS['db']['/usr/local/sbin/careertrellis-backup-health'], 'd'*64)
            data['artifact_hashes']['db']['/usr/local/sbin/unreviewed'] = 'e'*64
            with self.assertRaises(runtime.Refused):
                runtime.configure(SimpleNamespace(data=data), 'db')
        finally:
            for name,value in saved.items():setattr(runtime,name,value)


class ActivationEvidenceRPCTests(unittest.TestCase):
    def fixture(self):
        from deploy.onedrive_retention import activation_readiness as readiness
        now=dt.datetime.now(dt.timezone.utc)
        bindings={'standalone_manifest_sha256':'1'*64,'paired_manifest_sha256':'2'*64,
            'paired_policy_sha256':'b'*64,'standalone_policy_sha256':'e'*64}
        context=SimpleNamespace(bindings=bindings,revision='a'*40,document={'application_release':'d'*40})
        selector=SimpleNamespace(digest='f'*64,role='db',bindings=bindings)
        evidence={'schema':readiness.SCHEMA,'observed_at':now.isoformat(),'artifact_revision':'a'*40,
            'application_release':'d'*40,'baseline_bindings':copy.deepcopy(bindings),'catalog_sha256':'3'*64,
            'replacement_bundles':[{'archive_sha256':str(index+4)*64,'native_receipt_sha256':str(index+6)*64,
                'terminal_proof_sha256':str(index+8)*64,'backup_label':'20261026-155056F',
                'accepted_at':(now-dt.timedelta(hours=2-index)).isoformat()} for index in range(2)]}
        collector=Mock(return_value=evidence)
        held=[False]
        class Files:
            def __init__(self,fd):
                if fd!=7 or not held[0]:raise AssertionError('factory lost repository FD')
            def require_repository_lock(self):
                if not held[0]:raise AssertionError('repository fence lost')
        @contextmanager
        def repository_lock():
            held[0]=True
            try:yield 7,(1,2)
            finally:held[0]=False
        activation=SimpleNamespace(require_activated=Mock(return_value=selector))
        def current_backup(fd):
            Files(fd).require_repository_lock()
            return {'schema':'careertrellis.onedrive-current-backup/v1','backup_label':'20261026-155056F',
                'backup_completed_at':now.isoformat(),'observed_at':now.isoformat()}
        modules={'activation':activation,'activation_readiness':SimpleNamespace(native_replacements=collector,
            validate_evidence=readiness.validate_evidence,native_current_backup=Mock(side_effect=current_backup)),
            'catalog':SimpleNamespace(NativeFiles=Files)}
        loader=SimpleNamespace(_load=modules.__getitem__,verify_install=lambda:'a'*40,
                               _native_dependencies=lambda *args:context)
        request={'schema':db_control.SCHEMA,'transaction':'a'*32,'policy_sha256':'b'*64,
            'operation':db_control.RETENTION_ACTIVATION,
            'payload':{'retention_policy_sha256':'e'*64,'activation_sha256':'f'*64}}
        provider=SimpleNamespace(repository_lock=Mock(side_effect=repository_lock))
        return loader,request,SimpleNamespace(digest='e'*64),context,evidence,collector,provider,activation

    def test_closed_request_has_only_exact_policy_and_selector_no_paths_or_claimed_proof(self):
        loader,request,policy,context,evidence,collector,provider,activation=self.fixture()
        self.assertIs(db_control.validate_request(request),request)
        for extra in ('path','replacement_evidence','command'):
            changed=copy.deepcopy(request);changed['payload'][extra]={}
            with self.assertRaises(db_control.Refused):db_control.validate_request(changed)
        request['payload']['activation_sha256']='0'*64
        with self.assertRaises(db_control.Refused):db_control.validate_request(request)

    def test_fixed_native_factory_collects_actual_evidence_under_existing_fd_and_binds_reply(self):
        loader,request,policy,context,evidence,collector,provider,activation=self.fixture()
        with patch.object(entry,'_module',return_value=provider),patch.object(entry,'_control_lock') as control, \
                patch.object(entry,'_transaction_store') as transaction:
            answer=entry._retention_activation_evidence(loader,request,policy,context)
        collector.assert_called_once_with(7);control.assert_not_called();transaction.assert_not_called()
        self.assertEqual(answer['schema'],'careertrellis.onedrive-retention-scheduled-eligibility/v1')
        self.assertEqual(answer['transaction'],request['transaction'])
        self.assertEqual(answer['activation_sha256'],'f'*64)
        self.assertEqual(answer['replacement_evidence'],evidence)
        loader._load('activation_readiness').native_current_backup.assert_called_once_with(7)
        self.assertEqual(answer['current_backup']['backup_label'],'20261026-155056F')

    def test_wrong_selector_refuses_before_repository_or_native_collector(self):
        loader,request,policy,context,evidence,collector,provider,activation=self.fixture()
        request['payload']['activation_sha256']='c'*64
        with patch.object(entry,'_module',return_value=provider),self.assertRaises(entry.EntryRefused):
            entry._retention_activation_evidence(loader,request,policy,context)
        collector.assert_not_called();provider.repository_lock.assert_not_called()

    def test_missing_real_second_acceptance_stale_or_rebound_evidence_refuses(self):
        for mutate in (lambda e:e['replacement_bundles'].pop(),
            lambda e:e.update(observed_at='2020-01-01T00:00:00Z'),
            lambda e:e['baseline_bindings'].update(standalone_policy_sha256='f'*64)):
            loader,request,policy,context,evidence,collector,provider,activation=self.fixture();mutate(evidence)
            with patch.object(entry,'_module',return_value=provider),self.assertRaises(Exception):
                entry._retention_activation_evidence(loader,request,policy,context)
            collector.assert_called_once_with(7)

    def test_native_collector_failure_is_not_replaced_by_selector_claims(self):
        from deploy.onedrive_retention import activation_readiness
        loader,request,policy,context,evidence,collector,provider,activation=self.fixture()
        collector.side_effect=activation_readiness.ReadinessRefused()
        with patch.object(entry,'_module',return_value=provider),self.assertRaises(activation_readiness.ReadinessRefused):
            entry._retention_activation_evidence(loader,request,policy,context)
        collector.assert_called_once_with(7)


class BootstrapEvidenceRPCTests(unittest.TestCase):
    fixture=ActivationEvidenceRPCTests.fixture
    def bootstrap_fixture(self):
        from deploy.onedrive_retention import bundle
        loader,request,policy,context,evidence,collector,provider,activation=self.fixture()
        request['operation']=db_control.RETENTION_BOOTSTRAP
        request['payload']={'retention_policy_sha256':policy.digest}
        context.document['acceptance_receipts']={'ops':'9'*64}
        activation.routing_state=Mock(return_value=None)
        health=SimpleNamespace(_installation=Mock(return_value=({},'9'*64)))
        original=loader._load
        loader._load=lambda name:health if name=='health' else original(name)
        loader.canonical=bundle.canonical
        return loader,request,policy,context,evidence,collector,provider,activation,health

    def test_first_activation_request_cannot_include_paths_selector_or_caller_proof(self):
        loader,request,policy,context,evidence,collector,provider,activation,health=self.bootstrap_fixture()
        self.assertIs(db_control.validate_request(request),request)
        for key in ('activation_sha256','path','replacement_evidence'):
            value=copy.deepcopy(request);value['payload'][key]='a'*64
            with self.assertRaises(db_control.Refused):db_control.validate_request(value)

    def test_absent_markers_collect_real_proofs_and_role_bound_db_install_baseline(self):
        import hashlib
        loader,request,policy,context,evidence,collector,provider,activation,health=self.bootstrap_fixture()
        with patch.object(entry,'_module',return_value=provider),patch.object(entry,'_control_lock') as control, \
                patch.object(entry,'_transaction_store') as transaction:
            answer=entry._retention_bootstrap_evidence(loader,request,policy,context)
        collector.assert_called_once_with(7);control.assert_not_called();transaction.assert_not_called()
        self.assertEqual(answer['schema'],'careertrellis.onedrive-retention-bootstrap-evidence/v1')
        self.assertEqual(answer['db_install_receipt_sha256'],'9'*64)
        self.assertEqual(answer['db_baseline_sha256'],hashlib.sha256(loader.canonical(context.document)).hexdigest())
        self.assertEqual(answer['transaction'],request['transaction'])
        self.assertEqual(activation.routing_state.call_count,2)

    def test_partial_active_or_wrong_policy_refuses_before_collector(self):
        from deploy.onedrive_retention import activation as activation_module
        for mode in ('partial','active','policy'):
            loader,request,policy,context,evidence,collector,provider,activation,health=self.bootstrap_fixture()
            if mode=='partial':activation.routing_state.side_effect=activation_module.ActivationRefused()
            elif mode=='active':activation.routing_state.return_value=object()
            else:request['payload']['retention_policy_sha256']='f'*64
            with patch.object(entry,'_module',return_value=provider),self.assertRaises(Exception):
                entry._retention_bootstrap_evidence(loader,request,policy,context)
            collector.assert_not_called();provider.repository_lock.assert_not_called()

    def test_missing_second_or_native_factory_failure_cannot_return_bootstrap_evidence(self):
        from deploy.onedrive_retention import activation_readiness
        for fail in (False,True):
            loader,request,policy,context,evidence,collector,provider,activation,health=self.bootstrap_fixture()
            if fail:collector.side_effect=activation_readiness.ReadinessRefused()
            else:evidence['replacement_bundles'].pop()
            with patch.object(entry,'_module',return_value=provider),self.assertRaises(Exception):
                entry._retention_bootstrap_evidence(loader,request,policy,context)
            collector.assert_called_once_with(7)

    def test_marker_appearance_or_baseline_change_after_collection_refuses(self):
        for mode in ('marker','baseline'):
            loader,request,policy,context,evidence,collector,provider,activation,health=self.bootstrap_fixture()
            if mode=='marker':activation.routing_state.side_effect=[None,object()]
            else:loader._native_dependencies=lambda *args:SimpleNamespace(bindings={},document={})
            with patch.object(entry,'_module',return_value=provider),self.assertRaises(Exception):
                entry._retention_bootstrap_evidence(loader,request,policy,context)
            collector.assert_called_once_with(7)


if __name__ == '__main__':unittest.main()
