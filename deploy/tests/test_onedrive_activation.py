"""Activation claims and routing failure boundaries; no native action authority."""
import copy
import datetime as dt
import errno
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deploy.onedrive_retention import activation as a, bundle

NOW=dt.datetime(2026,10,27,12,tzinfo=dt.timezone.utc)
BINDINGS={name:str(index)*64 for index,name in enumerate(sorted(a.BINDINGS),1)}


def fixture():
    document={'standalone_revision':'a'*40,'application_release':'b'*40,
              'valid_until':'2027-01-23T00:00:00Z','acceptance_receipts':{'ops':'c'*64}}
    prepared=(NOW-dt.timedelta(minutes=5)).isoformat()
    intent={'schema':a.INTENT_SCHEMA,'transaction':'d'*32,'artifact_revision':'a'*40,
        'application_release':'b'*40,'baseline_bindings':copy.deepcopy(BINDINGS),
        'installation_receipts':{'web':'c'*64,'db':'e'*64},
        'replacement_bundles':[{'archive_sha256':digit*64,'native_receipt_sha256':str(index+4)*64,
            'terminal_proof_sha256':str(index+8)*64,'backup_label':'20261026-155056F',
            'accepted_at':(NOW-dt.timedelta(hours=2-index)).isoformat()} for index,digit in enumerate(('6','7'))],
        'reviews':{name:'8'*64 for name in a.REVIEWS},'prepared_at':prepared,
        'valid_until':'2027-01-22T00:00:00Z'}
    selector={'schema':a.SCHEMA,'transaction':intent['transaction'],
        'activation_intent_sha256':a.sha(bundle.canonical(intent)),'enabled':True,
        'activated_at':(NOW-dt.timedelta(minutes=1)).isoformat(),'valid_until':intent['valid_until']}
    return intent,selector,document


class ClaimTests(unittest.TestCase):
    def check(self,intent,selector,document):return a.validate(intent,selector,BINDINGS,document,'web',NOW)

    def test_complete_claims_are_never_authenticated_or_authorized(self):
        result=self.check(*fixture())
        self.assertEqual(result['replacement_count'],2)
        self.assertFalse(result['authenticated'] or result['dispatch_authorized'] or result['deletion_authorized'])

    def test_caller_cannot_construct_authenticated_selector(self):
        with self.assertRaises(a.ActivationRefused):a.AuthenticatedSelector({},'web',BINDINGS)

    def test_web_pending_fence_is_bound_to_exact_intent_source_and_activation_order(self):
        intent,selector,_=fixture()
        pending={'schema':a.PENDING_SCHEMA,'transaction':intent['transaction'],
            'intent_sha256':a.sha(bundle.canonical(intent)),'baseline_bindings':copy.deepcopy(BINDINGS),
            'source_manifest_sha256':BINDINGS['standalone_manifest_sha256'],
            'started_at':(NOW-dt.timedelta(minutes=3)).isoformat()}
        a.validate_pending(pending,intent,selector,BINDINGS)
        for field,value in (('transaction','f'*32),('intent_sha256','f'*64),
            ('source_manifest_sha256','f'*64),('started_at',NOW.isoformat())):
            changed=copy.deepcopy(pending);changed[field]=value
            with self.assertRaises(a.ActivationRefused):a.validate_pending(changed,intent,selector,BINDINGS)

    def test_selector_is_bound_to_exact_intent_and_enabled_state(self):
        for key,bad in (('activation_intent_sha256','f'*64),('transaction','f'*32),('enabled',False),
                        ('enabled',1),('schema','anything'),('valid_until','2027-01-21T00:00:00Z')):
            intent,selector,document=fixture();selector[key]=bad
            with self.subTest(key=key),self.assertRaises(a.ActivationRefused):self.check(intent,selector,document)

    def test_exact_two_distinct_accepted_proofs_and_no_guessed_or_zero_reviews(self):
        mutations=(lambda i:i['replacement_bundles'].pop(),
            lambda i:i['replacement_bundles'][1].update(archive_sha256=i['replacement_bundles'][0]['archive_sha256']),
            lambda i:i['replacement_bundles'][1].update(native_receipt_sha256=i['replacement_bundles'][0]['native_receipt_sha256']),
            lambda i:i['replacement_bundles'][1].update(terminal_proof_sha256=i['replacement_bundles'][0]['terminal_proof_sha256']),
            lambda i:i['replacement_bundles'].reverse(),
            lambda i:i['replacement_bundles'][0].update(native_receipt_sha256='0'*64),
            lambda i:i['replacement_bundles'][0].update(terminal_proof_sha256='unknown'),
            lambda i:i['replacement_bundles'][0].update(accepted_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda i:i['replacement_bundles'][0].update(backup_label='not-a-backup'),
            lambda i:i['reviews'].pop('rollback'),lambda i:i['reviews'].update(paired_transition='0'*64))
        for mutate in mutations:
            intent,selector,document=fixture();mutate(intent)
            selector['activation_intent_sha256']=a.sha(bundle.canonical(intent))
            with self.assertRaises(a.ActivationRefused):self.check(intent,selector,document)

    def test_release_revision_role_installation_and_current_binding_drift_refuse(self):
        for mutate in (lambda i:i.update(artifact_revision='f'*40),
            lambda i:i.update(application_release='f'*40),lambda i:i['installation_receipts'].update(web='f'*64),
            lambda i:i['baseline_bindings'].update(paired_policy_sha256='f'*64)):
            intent,selector,document=fixture();mutate(intent)
            selector['activation_intent_sha256']=a.sha(bundle.canonical(intent))
            with self.assertRaises(a.ActivationRefused):self.check(intent,selector,document)

    def test_lifetime_must_be_current_ordered_and_within_accepted_baseline(self):
        for mutate in (lambda i,s,d:s.update(activated_at=(NOW+dt.timedelta(seconds=1)).isoformat()),
            lambda i,s,d:s.update(activated_at=(NOW-dt.timedelta(minutes=10)).isoformat()),
            lambda i,s,d:d.update(valid_until=(NOW-dt.timedelta(seconds=1)).isoformat())):
            intent,selector,document=fixture();mutate(intent,selector,document)
            with self.assertRaises(a.ActivationRefused):self.check(intent,selector,document)


class RoutingTests(unittest.TestCase):
    def metadata(self,file=False,**changed):
        value=dict(st_dev=1,st_ino=2 if file else 1,st_mode=(stat.S_IFREG|0o600) if file else (stat.S_IFDIR|0o700),
            st_uid=0,st_gid=0,st_nlink=1 if file else 2,st_size=100,st_mtime_ns=1,st_ctime_ns=1)
        value.update(changed);return SimpleNamespace(**value)

    def state(self,present=()):
        def observed(path):
            if path==a.SELECTOR.parent:return self.metadata()
            if path in present:return self.metadata(file=True)
            raise FileNotFoundError(errno.ENOENT,'synthetic absent')
        return observed

    def test_only_both_absent_can_select_legacy_without_an_active_baseline(self):
        with patch.object(a,'_native_role',return_value='web'),patch.object(a.Path,'lstat',autospec=True,side_effect=self.state()), \
                patch.object(a.baseline,'native_context') as context:
            self.assertIsNone(a.routing_state())
        context.assert_not_called()

    def test_either_partial_file_refuses_before_baseline_or_provider(self):
        for present in ((True,False,False,False),(False,True,False,False),(True,True,False,False),
                        (False,False,True,False),(True,False,True,False),(False,True,True,False),
                        (False,False,False,True),(True,True,True,False)):
            with patch.object(a,'_native_role',return_value='web'), \
                    patch.object(a.Path,'lstat',autospec=True,side_effect=self.state(
                        tuple(path for path,exists in zip((a.INTENT,a.SELECTOR,a.FINALIZED,a.PENDING),present) if exists))), \
                    patch.object(a.baseline,'native_context') as context,self.assertRaises(a.ActivationRefused):a.routing_state()
            context.assert_not_called()

    def test_permission_io_and_non_enoent_absence_errors_never_select_legacy(self):
        for error in (PermissionError(errno.EACCES,'synthetic'),OSError(errno.EIO,'synthetic'),
                      FileNotFoundError(errno.EIO,'synthetic')):
            def observed(path):
                if path==a.SELECTOR.parent:return self.metadata()
                raise error
            with self.subTest(error=type(error).__name__),patch.object(a,'_native_role',return_value='web'), \
                    patch.object(a.Path,'lstat',autospec=True,side_effect=observed), \
                    patch.object(a.baseline,'native_context') as context,self.assertRaises(a.ActivationRefused):a.routing_state()
            context.assert_not_called()

    def test_symlink_or_unsafe_parent_is_not_absence(self):
        for bad_path in (a.INTENT,a.SELECTOR.parent):
            def observed(path):
                if path==bad_path:return self.metadata(file=True,st_mode=stat.S_IFLNK|0o777)
                return self.state()(path)
            with patch.object(a,'_native_role',return_value='web'), \
                    patch.object(a.Path,'lstat',autospec=True,side_effect=observed),self.assertRaises(a.ActivationRefused):a.routing_state()

    def test_appearance_or_parent_change_during_recheck_never_selects_legacy(self):
        calls={}
        for scenario in ('appears','parent-changes'):
            calls.clear()
            def observed(path):
                calls[path]=calls.get(path,0)+1
                if path==a.SELECTOR.parent:
                    return self.metadata(st_mtime_ns=2 if scenario=='parent-changes' and calls[path]>1 else 1)
                if scenario=='appears' and path==a.INTENT and calls[path]>1:return self.metadata(file=True)
                raise FileNotFoundError(errno.ENOENT,'synthetic absent')
            with patch.object(a,'_native_role',return_value='web'), \
                    patch.object(a.Path,'lstat',autospec=True,side_effect=observed),self.assertRaises(a.ActivationRefused):a.routing_state()

    def test_existing_consistent_files_require_the_native_selector_factory(self):
        selected=object();context=object()
        with patch.object(a,'_native_role',return_value='web'), \
                patch.object(a.Path,'lstat',autospec=True,side_effect=self.state((a.INTENT,a.SELECTOR,a.FINALIZED,a.PENDING))), \
                patch.object(a.baseline,'native_context',return_value=context), \
                patch.object(a,'require_activated',return_value=selected) as authenticate:
            self.assertIs(a.routing_state(),selected)
        authenticate.assert_called_once_with(context)

    def test_source_checkout_native_factory_refuses_before_config_read(self):
        with patch.object(a.entry,'_protected_document') as read,self.assertRaises(a.ActivationRefused):
            a.require_activated(object())
        read.assert_not_called()


class FinalizationTests(unittest.TestCase):
    def fixture(self):
        from deploy.onedrive_retention import activation_controller as c
        intent,selector,document=fixture()
        context=SimpleNamespace(role='web',bindings=BINDINGS,document=document)
        digest=lambda value:a.sha(bundle.canonical(value))
        owner={'pid':123,'start_ticks':456,'process_group':123,
               'boot_id':'12345678-1234-1234-1234-123456789abc'}
        completed=NOW-dt.timedelta(seconds=20)
        proofs={}
        for role in ('web','db'):
            receipt={'transaction':intent['transaction'],'role':role,'phase':'commit',
                'intent_sha256':digest(intent),'selector_sha256':digest(selector),'result':'committed',
                'committed_at':(NOW-dt.timedelta(seconds=40)).isoformat()}
            ack={'schema':c.ACK,'transaction':intent['transaction'],'role':role,
                'intent_sha256':digest(intent),'selector_sha256':digest(selector),'request_sha256':'9'*64,
                'receipt_sha256':digest(receipt),'receipt':receipt,
                'received_at':(NOW-dt.timedelta(seconds=30)).isoformat(),
                'provenance':c.provenance(role,owner,BINDINGS)}
            reconciliation={'schema':c.RECONCILIATION,'transaction':intent['transaction'],'role':role,
                'intent_sha256':digest(intent),'selector_sha256':digest(selector),
                'observed_at':completed.isoformat(),'committed_receipt_sha256':digest(receipt),
                'observation_sha256':'a'*64,'provenance':c.provenance(role,owner,BINDINGS)}
            proofs[role]={'commit':receipt,'ack':ack,'reconciliation':reconciliation}
        terminal={'schema':c.TERMINAL,'transaction':intent['transaction'],'intent_sha256':digest(intent),
            'selector_sha256':digest(selector),'baseline_bindings':copy.deepcopy(BINDINGS),
            'baseline_sha256':{'web':digest(document),'db':'b'*64},
            'installation_receipts':copy.deepcopy(intent['installation_receipts']),
            'committed_receipts':{role:digest(proofs[role]['commit']) for role in proofs},
            'commit_acknowledgements':{role:digest(proofs[role]['ack']) for role in proofs},
            'reconciliations':{role:digest(proofs[role]['reconciliation']) for role in proofs},
            'owner':owner,'source_manifest_sha256':BINDINGS['standalone_manifest_sha256'],
            'result':'both_commits_verified','unknown_outcome':False,
            'completed_at':completed.isoformat(),'prepare_deadline':(NOW+dt.timedelta(minutes=5)).isoformat()}
        final={name:copy.deepcopy(terminal[name]) for name in ('transaction','intent_sha256','selector_sha256',
            'baseline_bindings','baseline_sha256','installation_receipts','committed_receipts')}
        final.update(schema=c.FINALIZED,controller_terminal_sha256=digest(terminal),
            finalized_at=(NOW-dt.timedelta(seconds=10)).isoformat(),valid_until=intent['valid_until'])
        return final,terminal,proofs,intent,selector,context

    def test_complete_claims_do_not_authenticate_or_authorize(self):
        result=a.validate_finalized(*self.fixture(),NOW)
        self.assertFalse(result['authenticated'] or result['dispatch_authorized'])

    def test_missing_or_changed_commit_acknowledgement_fences_finalization(self):
        for change in ('missing','changed','cross-role'):
            values=list(self.fixture());proofs=values[2]
            if change=='missing':proofs['db'].pop('ack')
            elif change=='changed':proofs['db']['ack']['receipt']['result']='unknown'
            else:proofs['db']['ack']=copy.deepcopy(proofs['web']['ack'])
            with self.subTest(change=change),self.assertRaises(ValueError):a.validate_finalized(*values,NOW)

    def test_missing_native_final_record_never_yields_selector(self):
        values=self.fixture();context=values[-1]
        with patch.object(a.entry,'FILES',{'activation_controller.py','activation_transition.py'}), \
                patch.object(a.entry,'_protected_document',side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):a.require_finalized(context,values[3],values[4])

    def test_unknown_wrong_baseline_expired_or_unbound_terminal_refuses(self):
        digest=lambda value:a.sha(bundle.canonical(value))
        for change in ('unknown','baseline','late','source'):
            values=list(self.fixture());final,terminal=values[:2]
            if change=='unknown':terminal['unknown_outcome']=True
            elif change=='baseline':terminal['baseline_sha256']['web']=final['baseline_sha256']['web']='f'*64
            elif change=='late':final['finalized_at']=(NOW+dt.timedelta(seconds=1)).isoformat()
            else:terminal['source_manifest_sha256']='f'*64
            final['controller_terminal_sha256']=digest(terminal)
            with self.subTest(change=change),self.assertRaises(ValueError):a.validate_finalized(*values,NOW)
