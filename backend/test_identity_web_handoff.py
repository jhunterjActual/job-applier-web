"""Synthetic-only file publication failures; native Linux uses real protected I/O."""
from contextlib import ExitStack, nullcontext
import os
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from cryptography.fernet import Fernet
import admission_operations as ops
import identity_configuration as configuration
import identity_entry
import identity_web_handoff as handoff
import session_v2_transition as transition
from workos_transport_candidate import ProviderSettings


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        native=os.name=='posix' and os.geteuid()==0
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir='/' if native else None,prefix='ct-handoff-')))
        self.stack.enter_context(patch.object(ops,'ROOT',self.root))
        self.stack.enter_context(patch.object(ops,'STATE',self.root))
        self.stack.enter_context(patch.object(ops,'AUTH_FILE',self.root/'auth-v2.env'))
        self.stack.enter_context(patch.object(handoff,'CURRENT',Mock(resolve=lambda:handoff.RELEASES/('b'*40))))
        self.stack.enter_context(patch.object(handoff,'inventory',return_value={'synthetic':'fixed'}))
        self.stack.enter_context(patch.object(handoff,'locks',side_effect=nullcontext))
        self.stack.enter_context(patch.object(transition,'boot',return_value='synthetic-boot'))
        self.stack.enter_context(patch.object(ops,'stopped_services'))
        self.stack.enter_context(patch.object(ops,'check_worker_delivery'))
        if not native:
            self.stack.enter_context(patch.object(ops,'read_protected',side_effect=lambda path,**kw:path.read_bytes()))
            self.stack.enter_context(patch.object(ops,'write_protected',side_effect=self.write))
            self.stack.enter_context(patch.object(ops,'protected_parents'))
            self.stack.enter_context(patch.object(handoff,'paths',side_effect=lambda p:(
                self.root/('provider-config-'+p['operation']+'.sealed'),self.root/('provider-config-'+p['operation']+'.json'))))
        self.key=Fernet.generate_key().decode()
        auth={'CAREERTRELLIS_AUTH_V2_'+k:v for k,v in {'ENABLED':'0','ISSUER':'https://api.workos.com/user_management/client_synthetic_old',
            'GENERATION':'synthetic-old','KEY_ID':'key1','SESSION_SECONDS':'3600','ENVELOPE_KEY':self.key,'FLOW_KEY':Fernet.generate_key().decode()}.items()}
        auth['CAREERTRELLIS_ADMISSION_DATABASE_URL']='postgresql+psycopg://careertrellis_admission_broker:synthetic@10.70.0.2/careertrellis'
        files={name:'' for name in handoff.FILES}
        files['staging.env']='# preserve this comment\nWORKOS_CLIENT_ID=client_synthetic_old\nWORKOS_API_KEY=synthetic-old-key\nCAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID=user_synthetic_old\nWORKOS_COOKIE_PASSWORD='+Fernet.generate_key().decode()+'\n'
        files['auth-v2.env']=''.join(k+'='+v+'\n' for k,v in auth.items())
        files['admission-web.json']=ops.encoded({'issuer':auth[handoff.PREFIX+'ISSUER'],'generation':'synthetic-old','key_id':'key1','session_seconds':3600}).decode()
        files['postgres-runtime.env']='CAREERTRELLIS_DATABASE_URL=postgresql+psycopg://careertrellis_runtime:synthetic@10.70.0.2/careertrellis\n'
        for name,raw in files.items(): ops.write_protected(self.root/name,raw.encode())
        source=handoff.validate_files(files)[0]
        target=ProviderSettings('client_synthetic_new','synthetic-new-key','https://api.workos.com/user_management/client_synthetic_new','synthetic-new')
        operation=str(uuid4())
        self.p={'version':1,'operation':operation,'mode':'switch_environment','source_application':'b'*40,
            'application':'b'*40,'operations':'c'*40,'baseline_binding':source.binding,'target_binding':target.binding,
            'expected_generation':3,'owner_proof':transition.owner_proof(operation,target.binding,'user_synthetic_new'),
            'transitions':[str(uuid4())],'quarantined_accounts':[],'authorized':True}
        self.a={'operation':operation,'application':'b'*40,'operations':'c'*40,'provider_binding':target.binding,
            'owner_proof':self.p['owner_proof'],'realm':'synthetic-new','schema':transition.HEAD,'cap':25,'ttl':600,'session_seconds':3600}
        self.before=files
        self.db=self.stack.enter_context(patch.object(configuration,'apply',return_value={'outcome':'configuration_committed_maintenance_retained'}))
        self.guard=self.stack.enter_context(patch.object(handoff,'committed_guard',side_effect=lambda *a:nullcontext()))
        self.actual_preflight=transition.web_enable
        self.preflight=self.stack.enter_context(patch.object(transition,'web_enable'))

    def write(self,path,raw,*,previous=None,**kw):
        if previous is None: self.assertFalse(path.exists() or path.is_symlink())
        else: self.assertEqual(previous,path.read_bytes())
        path.write_bytes(raw)

    def answers(self):
        return ['VISIBLE',self.p['operation'],'AUTHORIZED','client_synthetic_new','synthetic-new-key','user_synthetic_new',
            'synthetic.authkit.com','VERIFIED','NONE','SAVE']

    def test_provisioned_disabled_key_is_preserved_and_enabled_0015_stops_before_prompt(self):
        files=dict(self.before)
        suffix='# enrollment remains off\n'+handoff.PREFIX+'TOTP_ENABLED=0\n'+handoff.PREFIX+'TOTP_KEY='+Fernet.generate_key().decode()+'\n'
        files['auth-v2.env']+=suffix
        answers=self.answers()
        target=handoff.candidate(self.p,self.a,files,lambda prompt:answers.pop(0))
        self.assertTrue(target['auth-v2.env'].endswith(suffix))
        files['auth-v2.env']=files['auth-v2.env'].replace('TOTP_ENABLED=0','TOTP_ENABLED=1')
        ask=Mock()
        with self.assertRaises(ValueError): handoff.candidate(self.p,self.a,files,ask)
        ask.assert_not_called()

    def test_bad_optional_enrollment_fields_reject(self):
        for suffix in ('TOTP_ENABLED=yes\n','TOTP_KEY=bad\n','TOTP_KEY='+self.key+'\n','TOTP_UNKNOWN=0\n'):
            files=dict(self.before); files['auth-v2.env']+=handoff.PREFIX+suffix
            with self.subTest(field=suffix.split('=')[0]):
                with self.assertRaises((ValueError,ops.OperationError)): handoff.validate_files(files)

    def test_managed_hostname_accepts_both_suffixes_without_changing_target_binding(self):
        targets=[]
        for hostname in ('synthetic.authkit.com','synthetic-editor-68.authkit.app'):
            with self.subTest(hostname=hostname):
                values=self.answers(); values[6]=hostname
                targets.append(handoff.candidate(self.p,self.a,self.before,lambda prompt:values.pop(0)))
                self.assertEqual(self.before,handoff.read_files())
        self.assertEqual(targets[0],targets[1])
        self.db.assert_not_called()

    def test_invalid_managed_hostname_stops_before_final_confirmation_or_write(self):
        for hostname in ('https://synthetic.authkit.app','synthetic.authkit.app/',
                'synthetic.authkit.app.attacker.example','synthetic.attacker.app',
                'nested.synthetic.authkit.app','synthetic.authkit.app:443',
                '-synthetic.authkit.app','synthetic-.authkit.app',
                'a'*64+'.authkit.app','synthetic.authkit.app\n'):
            with self.subTest(hostname=hostname):
                values=self.answers(); values[6]=hostname
                with self.assertRaises(ops.OperationError):
                    handoff.candidate(self.p,self.a,self.before,lambda prompt:values.pop(0))
                self.assertEqual(['VERIFIED','NONE','SAVE'],values)
                self.assertEqual(self.before,handoff.read_files())
        self.db.assert_not_called()

    def prepare(self):
        values=self.answers(); terminal=Mock()
        terminal.ask.side_effect=lambda *a,**kw:values.pop(0)
        with patch.object(identity_entry,'SecureTerminal') as cls:
            cls.return_value.__enter__.return_value=terminal
            result=handoff.prepare(self.p,self.a)
        self.assertEqual('encrypted_candidate_saved_no_configuration_changed',result['outcome'])
        self.assertEqual(self.before,handoff.read_files())
        self.db.assert_not_called()
        return ops.strict_json(Fernet(self.key.encode()).decrypt(ops.read_protected(handoff.paths(self.p)[0],limit=524288)))['after']

    def test_prepare_encrypts_secrets_and_changes_only_allowed_fields(self):
        after=self.prepare()
        raw=handoff.paths(self.p)[0].read_bytes()
        for value in (b'synthetic-new-key',b'user_synthetic_new',b'client_synthetic_old'): self.assertNotIn(value,raw)
        self.assertTrue(after['staging.env'].startswith('# preserve this comment\n'))
        for name in handoff.FILES:
            if name not in handoff.MUTABLE: self.assertEqual(self.before[name],after[name])
        old=ops.assignments(self.before['auth-v2.env'].encode()); new=ops.assignments(after['auth-v2.env'].encode())
        for key in ('ENABLED','SESSION_SECONDS','ENVELOPE_KEY','FLOW_KEY','KEY_ID'):
            self.assertEqual(old[handoff.PREFIX+key],new[handoff.PREFIX+key])

    def test_selection_changed_during_prompt_or_before_publish_refuses_effects(self):
        import identity_plan_store
        terminal=Mock(); values=self.answers()
        terminal.ask.side_effect=lambda *a,**kw:values.pop(0)
        with patch.object(identity_entry,'SecureTerminal') as cls, \
                patch.object(identity_plan_store,'require_current',side_effect=ops.OperationError(ops.ERROR)):
            cls.return_value.__enter__.return_value=terminal
            with self.assertRaises(ops.OperationError): handoff.prepare(self.p,self.a,selection=b'synthetic-stale')
            self.assertFalse(handoff.paths(self.p)[0].exists())
            with self.assertRaises(ops.OperationError): handoff.publish(self.p,self.a,None,selection=b'synthetic-stale')
        self.db.assert_not_called()
        self.assertEqual(self.before,handoff.read_files())

    @unittest.skipUnless(importlib.util.find_spec('workos'),'Application dependencies required')
    def test_advance_preflight_validates_sealed_target_without_switching_current(self):
        self.p['application']='d'*40; self.a['application']='d'*40
        self.prepare()
        with patch.object(ops,'write_protected',side_effect=AssertionError('read-only preflight')):
            result=handoff.prepared_preflight(self.p,self.a,None)
        self.assertEqual('sealed_candidate_configuration_valid_source_unchanged',result['outcome'])
        self.assertEqual(handoff.RELEASES/self.p['source_application'],handoff.CURRENT.resolve())
        self.assertEqual(self.before,handoff.read_files()); self.db.assert_not_called()
        with patch.object(transition,'boot',return_value='different-boot'),self.assertRaises(ops.OperationError):
            handoff.prepared_preflight(self.p,self.a,None)

    @unittest.skipUnless(importlib.util.find_spec('workos'),'Application dependencies required')
    def test_candidate_and_installed_preflight_share_input_rejection(self):
        after=self.prepare()
        files={name:after[name].encode() for name in transition.WEB_INPUTS}
        transition.validate_web_inputs(self.a,files)
        for name,raw in (('provider-secrets.env',b'WORKOS_API_KEY=synthetic-override\n'),
                ('auth-v2.env',files['auth-v2.env'].replace(b'SESSION_SECONDS=3600',b'SESSION_SECONDS=7200'))):
            with self.assertRaises(ops.OperationError): transition.validate_web_inputs(self.a,{**files,name:raw})

    def test_every_partial_write_and_lost_receipt_resumes_same_target(self):
        after=self.prepare(); original=ops.write_protected
        for fail_after in range(1,5):
            for name,raw in self.before.items(): (self.root/name).write_bytes(raw.encode())
            receipt=handoff.paths(self.p)[1]
            if receipt.exists(): receipt.unlink()
            counter=[0]
            def interrupted(path,raw,**kw):
                original(path,raw,**kw); counter[0]+=1
                if counter[0]==fail_after: raise OSError('synthetic interrupted acknowledgement')
            with patch.object(ops,'write_protected',side_effect=interrupted),self.assertRaises(OSError):
                handoff.publish(self.p,self.a,None)
            handoff.publish(self.p,self.a,None)
            self.assertEqual(after,handoff.read_files())
            self.assertEqual('configured_services_stopped',ops.strict_json(receipt.read_bytes())['phase'])
        self.assertEqual(self.p['baseline_binding'],self.db.call_args.kwargs['verified_current_binding'])

    def test_drift_unknown_mixture_and_changed_boot_block_before_database(self):
        self.prepare()
        path=self.root/'staging.env'; baseline=path.read_bytes()
        path.write_bytes(baseline+b'UNRELATED=drift\n')
        with self.assertRaises(ops.OperationError): handoff.publish(self.p,self.a,None)
        self.db.assert_not_called(); path.write_bytes(baseline)
        with patch.object(transition,'boot',return_value='different'),self.assertRaises(ops.OperationError):
            handoff.publish(self.p,self.a,None)
        self.db.assert_not_called()

    def test_database_rejection_and_stale_committed_guard_never_publish(self):
        self.prepare()
        self.db.side_effect=ops.OperationError(ops.ERROR)
        with self.assertRaises(ops.OperationError): handoff.publish(self.p,self.a,None)
        self.assertEqual(self.before,handoff.read_files())
        self.db.side_effect=None; self.guard.side_effect=ops.OperationError(ops.ERROR)
        with self.assertRaises(ops.OperationError): handoff.publish(self.p,self.a,None)
        self.assertEqual(self.before,handoff.read_files())

    def test_explicit_reconciliation_after_reboot_never_resubmits_database(self):
        after=self.prepare()
        with patch.object(transition,'boot',return_value='new-boot'):
            handoff.publish(self.p,self.a,None,reconcile=True)
        self.db.assert_not_called()
        self.assertEqual(after,handoff.read_files())

    def test_bind_current_preserves_all_bytes_and_requires_existing_owner(self):
        p={**self.p,'mode':'bind_current','target_binding':self.p['baseline_binding'],'transitions':[]}
        p['owner_proof']=transition.owner_proof(p['operation'],p['target_binding'],'user_synthetic_old')
        a={**self.a,'realm':'synthetic-old','provider_binding':p['target_binding'],'owner_proof':p['owner_proof']}
        values=['VISIBLE',p['operation'],'AUTHORIZED','SAVE']
        self.assertEqual(self.before,handoff.candidate(p,a,self.before,lambda prompt:values.pop(0)))

    def test_cancel_or_worker_secret_overlay_cannot_save(self):
        values=self.answers(); values[-1]='CANCEL'
        with self.assertRaises(ops.OperationError): handoff.candidate(self.p,self.a,self.before,lambda prompt:values.pop(0))
        altered={**self.before,'postgres-worker.env':'WORKOS_API_KEY=synthetic-wrong\n'}
        ask=Mock()
        with self.assertRaises(ops.OperationError): handoff.candidate(self.p,self.a,altered,ask)
        ask.assert_not_called()

    def test_run_sanitizes_validation_and_crypto_errors(self):
        with patch.object(handoff,'plans',side_effect=ValueError('synthetic-private-value')):
            with self.assertRaises(ops.OperationError) as error: handoff.run('enter',None)
        self.assertNotIn('synthetic-private-value',str(error.exception))

    @unittest.skipUnless(importlib.util.find_spec('workos'), 'Application dependency runtime required; covered by Windows application environment')
    def test_published_target_passes_actual_disabled_v2_preflight(self):
        self.prepare()
        self.preflight.side_effect=self.actual_preflight
        original=Path.resolve
        with patch.object(Path,'resolve',lambda path,*a,**kw:handoff.RELEASES/self.p['application']
                if str(path).replace('\\','/')=='/opt/careertrellis/staging/current' else original(path,*a,**kw)):
            handoff.publish(self.p,self.a,None)
        self.preflight.assert_called_once_with(self.a,preflight=True)


class UnitInventoryTests(unittest.TestCase):
    def test_actual_three_service_templates_and_authority_guards(self):
        source=Path(__file__).absolute().parent.parent/'deploy/systemd'
        installed={}; properties={}
        for name in ops.SERVICES:
            raw=(source/name).read_bytes()
            guard=(source/(name+'.d/hosted-authority.conf')).read_bytes()
            installed['/etc/systemd/system/'+name]=raw
            installed['/etc/systemd/system/'+name+'.d/hosted-authority.conf']=guard
            entries=[line.split('=',1)[1] for line in raw.decode().splitlines() if line.startswith('EnvironmentFile=')]
            properties[name]={'NeedDaemonReload':'no','DropInPaths':'/etc/systemd/system/'+name+'.d/hosted-authority.conf',
                'FragmentPath':'/etc/systemd/system/'+name,'PassEnvironment':'','Environment':'PYTHONDONTWRITEBYTECODE=1',
                'EnvironmentFiles':'\n'.join(x.lstrip('-')+' (ignore_errors='+('yes' if x.startswith('-') else 'no')+')' for x in entries)}
        def read(path,**kw): return installed[path.as_posix()] if path.as_posix() in installed else path.read_bytes()
        with patch.object(ops,'read_protected',side_effect=read),patch.object(ops,'command',side_effect=lambda argv:
                properties[argv[2]][argv[-2].split('=')[1]].encode()):
            result=handoff.inventory()
            self.assertEqual(set(ops.SERVICES),set(result))
            for name in ops.SERVICES:
                guard='check-web-runtime' if name=='careertrellis-web.service' else 'check-worker-runtime'
                self.assertIn(guard,result[name]['authority_guard'])
            properties['careertrellis-worker.service']['EnvironmentFiles']+='\n/etc/careertrellis/auth-v2.env (ignore_errors=yes)'
            with self.assertRaises(ops.OperationError): handoff.inventory()

    def test_reject_extra_inputs_dropins_reload_and_modified_unit(self):
        template=b'EnvironmentFile=/etc/careertrellis/staging.env\nEnvironmentFile=-/etc/careertrellis/auth-v2.env\n'
        props={'NeedDaemonReload':'no','DropInPaths':'/etc/systemd/system/careertrellis-web.service.d/hosted-authority.conf','PassEnvironment':'',
            'FragmentPath':'/etc/systemd/system/careertrellis-web.service',
            'EnvironmentFiles':'/etc/careertrellis/staging.env (ignore_errors=no)\n/etc/careertrellis/auth-v2.env (ignore_errors=yes)',
            'Environment':'PYTHONDONTWRITEBYTECODE=1'}
        with patch.object(ops,'SERVICES',('careertrellis-web.service',)),patch.object(ops,'read_protected',return_value=template),\
                patch.object(ops,'command',side_effect=lambda argv:props[argv[-2].split('=')[1]].encode()):
            self.assertEqual({'careertrellis-web.service':{'fragment':template.decode(),'authority_guard':template.decode()}},handoff.inventory())
            for key,value in (('NeedDaemonReload','yes'),('DropInPaths','/run/extra.conf'),
                    ('EnvironmentFiles',props['EnvironmentFiles']+' /etc/override.env (ignore_errors=no)'),
                    ('Environment','WORKOS_API_KEY=synthetic'),('PassEnvironment','WORKOS_API_KEY'),('DropInPaths','')):
                with patch.dict(props,{key:value}),self.assertRaises(ops.OperationError): handoff.inventory()
            with patch.object(ops,'read_protected',side_effect=[template,template+b'Environment=OTHER=1\n']),self.assertRaises(ops.OperationError):
                handoff.inventory()
            with patch.object(ops,'read_protected',side_effect=[template,template,template,template+b'ExecStartPre=unexpected\n']),self.assertRaises(ops.OperationError):
                handoff.inventory()


@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:-1)()==0,'Native root Linux required')
class NativeLockTests(unittest.TestCase):
    def test_rotation_overlap_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory(dir='/',prefix='ct-handoff-lock-') as directory:
            path=Path(directory)/'rotation.lock'
            with patch.object(handoff,'ROTATION',path),patch.object(ops,'host_locks',side_effect=nullcontext):
                with handoff.locks():
                    with self.assertRaises(BlockingIOError):
                        with handoff.locks(): self.fail('overlapping rotation accepted')
                path.unlink(); target=Path(directory)/'target'; target.write_bytes(b'')
                path.symlink_to(target)
                with self.assertRaises(OSError):
                    with handoff.locks(): self.fail('symlink accepted')


if __name__=='__main__': unittest.main()
