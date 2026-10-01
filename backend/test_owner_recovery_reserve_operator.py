"""Offline relay protocol/source/journal checks; never calls SSH or provider."""
from copy import deepcopy
import base64
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import owner_recovery_reserve_operator as operator


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name).absolute();self.bundle=root/('c'*40)
        (self.bundle/'backend').mkdir(parents=True)
        self.journal=root/'journal';self.journal.mkdir(mode=0o700)
        wrappers={}
        for name in operator.MODULES:
            raw=Path(operator.__file__).read_bytes() if name=='owner_recovery_reserve_operator' else b'# synthetic reviewed source\n'
            path=self.bundle/'backend'/(name+'.py');path.write_bytes(raw)
            wrappers['backend/'+name+'.py']=operator.digest(raw)
        manifest={'version':1,'operations':operator.OPS,'wrapper':wrappers,
            'library':{name:'a'*64 for name in ('backend/admission_operations.py',
                'backend/identity_plan_store.py','backend/identity_plan_packet.py')}}
        raw=operator.encoded(manifest)+b'\n';(self.bundle/'source-manifest.json').write_bytes(raw)
        self.pin=operator.digest(raw);self.clock=100.0
        selection=b'{"synthetic":"private selected plan"}'
        self.scope={'operation':'11111111-1111-4111-8111-111111111111',
            'selection_sha256':operator.digest(selection),'activation_sha256':'b'*64,
            'enrollment_sha256':'e'*64,'closure_sha256':self.pin,'actions':['consume','receipt','enter','attest']}
        self.context={key:value for key,value in self.scope.items() if key!='actions'}
        self.context.update(action='consume',stage='pre',selection_hex=selection.hex())
        self.req={'version':1,'id':'22222222-2222-4222-8222-222222222222',
            'created':99.0,'expires':129.0,'context':self.context}
        self.calls=[];self.responses=[]
        self.relay=operator.Relay(bundle_directory=self.bundle,manifest_sha256=self.pin,scope=self.scope,
            journal_directory=self.journal,runner=self.runner,now=lambda:self.clock,monotonic=lambda:self.clock,
            sleep=lambda seconds:setattr(self,'clock',self.clock+seconds))
    def runner(self,args,payload,timeout):
        self.calls.append((args,json.loads(payload),timeout))
        value=self.responses.pop(0)
        if isinstance(value,BaseException):raise value
        if callable(value):value=value()
        return operator.encoded(value)
    def pending(self):return {'status':'pending','request':deepcopy(self.req)}
    def db(self):return {key:self.context[key] for key in ('selection_hex','selection_sha256','activation_sha256')}
    def plan(self,last=None):self.responses=[self.pending(),self.db(),self.pending(),last or {'status':'published'}]
    def test_success_claims_before_db_and_digest_before_publish(self):
        def db():
            self.assertTrue(self.relay.journal.path(self.req['id'],'claim').exists());return self.db()
        def published():
            self.assertTrue(self.relay.journal.path(self.req['id'],'observation').exists());return {'status':'published'}
        self.responses=[self.pending(),db,self.pending(),published]
        self.assertEqual('published',self.relay.once())
        self.assertEqual([call[0][-2] for call in self.calls],
            [operator.HOSTS['web'],operator.HOSTS['db'],operator.HOSTS['web'],operator.HOSTS['web']])
        for args,envelope,timeout in self.calls:
            self.assertIn('StrictHostKeyChecking=yes',args);self.assertIn('ForwardAgent=no',args)
            self.assertNotIn(self.context['selection_hex'],' '.join(args));self.assertLess(len(args[-1]),1024)
            self.assertLessEqual(timeout,15);self.assertTrue(base64.b64decode(envelope['code']))
        for path in self.journal.iterdir():
            self.assertNotIn(b'private selected plan',path.read_bytes())
            self.assertNotIn(self.context['selection_hex'].encode(),path.read_bytes())
    def test_idle_does_not_claim_or_read_db(self):
        self.responses=[{'status':'idle'}];self.assertEqual('idle',self.relay.once())
        self.assertFalse(list(self.journal.iterdir()));self.assertEqual(1,len(self.calls))
    def test_unapproved_scope_is_never_inferred_from_request(self):
        for key,value in (('operation','33333333-3333-4333-8333-333333333333'),
            ('activation_sha256','d'*64),('closure_sha256','d'*64),('enrollment_sha256','d'*64),('action','reset')):
            request=deepcopy(self.req);request['context'][key]=value
            self.responses=[{'status':'pending','request':request}]
            with self.assertRaises(ValueError):self.relay.once()
        self.assertFalse(list(self.journal.iterdir()))
    def test_expired_future_or_oversized_window_refused(self):
        for created,expires in ((50,80),(101,120),(90,121)):
            req={**self.req,'created':created,'expires':expires};self.responses=[{'status':'pending','request':req}]
            with self.assertRaises(ValueError):self.relay.once()
        self.assertFalse(list(self.journal.iterdir()))
    def test_database_mismatch_stops_without_publication(self):
        self.responses=[self.pending(),{**self.db(),'activation_sha256':'d'*64}]
        with self.assertRaises(ValueError):self.relay.once()
        self.assertEqual(2,len(self.calls));self.assertTrue(self.relay.journal.path(self.req['id'],'claim').exists())
        self.assertFalse(self.relay.journal.path(self.req['id'],'observation').exists())
    def test_changed_request_at_repeek_stops(self):
        changed=self.pending();changed['request']['id']='33333333-3333-4333-8333-333333333333'
        self.responses=[self.pending(),self.db(),changed]
        with self.assertRaises(ValueError):self.relay.once()
        self.assertEqual(3,len(self.calls))
    def test_failure_after_claim_reconciles_without_observation(self):
        self.responses=[self.pending(),RuntimeError('synthetic DB failure')]
        with self.assertRaises(ValueError):self.relay.once()
        self.clock=150
        self.responses=[{'status':'expired','request':self.req,'response':None,
            'currently_fresh':False,'authorization':False}]
        self.assertEqual({'status':'expired','authorization':False},self.relay.reconcile(self.req['id']))
        self.assertEqual(3,len(self.calls));self.assertEqual(['relay-status',self.req['id']],self.calls[-1][1]['request']['argv'])
    def test_claim_only_with_unexpected_response_refuses(self):
        self.responses=[self.pending(),RuntimeError('synthetic DB failure')]
        with self.assertRaises(ValueError):self.relay.once()
        self.responses=[{'status':'answered','request':self.req,'response':{},
            'currently_fresh':True,'authorization':False}]
        with self.assertRaises(ValueError):self.relay.reconcile(self.req['id'])
        self.assertFalse(self.relay.journal.path(self.req['id'],'reconciled').exists())
    def test_duplicate_claim_never_repeats_db_or_publish(self):
        self.plan();self.relay.once();self.responses=[self.pending()]
        with self.assertRaises(ValueError):self.relay.once()
        self.assertEqual(5,len(self.calls))
    def test_lost_ack_reconciles_only_one_status_read(self):
        self.plan(RuntimeError('private failure must not escape'))
        with self.assertRaisesRegex(ValueError,'^'+operator.ERROR.replace('.','[.]')+'$'):self.relay.once()
        publication=self.calls[-1][1]['request']
        observation=json.loads(base64.b64decode(publication['input']))['observation']
        self.clock=150
        self.responses=[{'status':'answered','request':self.req,'response':{'version':1,
            'request_id':self.req['id'],'observed':101,'observation':observation},
            'currently_fresh':False,'authorization':False}]
        self.assertEqual({'status':'answered','authorization':False},self.relay.reconcile(self.req['id']))
        self.assertEqual(['relay-status',self.req['id']],self.calls[-1][1]['request']['argv'])
        self.assertEqual(5,len(self.calls))
    def test_wrapper_source_drift_refused_before_ssh(self):
        (self.bundle/'backend/owner_recovery_reserve.py').write_bytes(b'changed')
        with self.assertRaises(ValueError):self.relay.once()
        self.assertFalse(self.calls)
    def test_own_source_pin_must_match_running_driver(self):
        path=self.bundle/'backend/owner_recovery_reserve_operator.py';path.write_bytes(b'fake driver')
        raw=json.loads((self.bundle/'source-manifest.json').read_bytes());raw['wrapper']['backend/owner_recovery_reserve_operator.py']=operator.digest(path.read_bytes())
        content=operator.encoded(raw);(self.bundle/'source-manifest.json').write_bytes(content)
        scope={**self.scope,'closure_sha256':operator.digest(content)}
        with self.assertRaises(ValueError):operator.Relay(bundle_directory=self.bundle,manifest_sha256=scope['closure_sha256'],
            scope=scope,journal_directory=self.journal,runner=self.runner)
        self.assertFalse(self.calls)
    def test_bounded_loop_idle_polls_do_not_retry_failure(self):
        self.responses=[{'status':'idle'},{'status':'idle'}]
        self.assertEqual('relay_window_ended',self.relay.run(seconds=1,poll=.5)['outcome'])
        self.assertEqual(2,len(self.calls))
        with self.assertRaises(ValueError):self.relay.run(seconds=1)
    def test_remote_sources_compile_without_running(self):
        compile(operator.WEB,'web','exec');compile(operator.DB,'db','exec');compile(operator.BOOT,'boot','exec')
    def test_duplicate_keys_and_nonfinite_json_refused(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}'):
            with self.assertRaises(ValueError):operator.strict(raw)

    def test_cli_requires_independent_scope_digest_and_sanitizes_errors(self):
        path=self.bundle.parent/'scope.json';raw=operator.encoded(self.scope);path.write_bytes(raw)
        args=['--bundle',str(self.bundle),'--manifest-sha256',self.pin,'--scope-file',str(path),
            '--scope-sha256',operator.digest(raw),'--journal-dir',str(self.journal),'--seconds','30']
        mock=Mock();mock.run.return_value={'outcome':'relay_window_ended','published':0,'authorization':False}
        with patch.object(operator,'Relay',return_value=mock),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(0,operator.main(args));mock.run.assert_called_once_with(seconds=30)
            self.assertNotIn(self.scope['operation'],out.getvalue())
        with patch.object(operator,'Relay') as factory,patch('sys.stderr',new_callable=io.StringIO) as error:
            path.write_bytes(raw+b' ');self.assertEqual(2,operator.main(args));factory.assert_not_called()
            self.assertEqual(operator.ERROR+'\n',error.getvalue())
    def test_cli_reconcile_never_runs_loop(self):
        path=self.bundle.parent/'scope.json';raw=operator.encoded(self.scope);path.write_bytes(raw)
        args=['--bundle',str(self.bundle),'--manifest-sha256',self.pin,'--scope-file',str(path),
            '--scope-sha256',operator.digest(raw),'--journal-dir',str(self.journal),'--reconcile',self.req['id']]
        mock=Mock();mock.reconcile.return_value={'status':'answered','authorization':False}
        with patch.object(operator,'Relay',return_value=mock),patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(0,operator.main(args));mock.run.assert_not_called()
            mock.reconcile.assert_called_once_with(self.req['id'])


class PipeTests(unittest.TestCase):
    def process(self,stdout=b'{}',stderr=b''):
        value=Mock();value.stdin=io.BytesIO();value.stdout=io.BytesIO(stdout);value.stderr=io.BytesIO(stderr)
        value.wait.return_value=0;return value
    def test_bounded_pipes_return_only_captured_stdout(self):
        process=self.process()
        with patch.object(operator.subprocess,'Popen',return_value=process):
            self.assertEqual(b'{}',operator.bounded_run(['synthetic'],b'payload',1))
        process.kill.assert_not_called()
    def test_any_stderr_or_oversized_stdout_refuses(self):
        for process in (self.process(stderr=b'private diagnostic'),self.process(stdout=b'x'*32769)):
            with patch.object(operator.subprocess,'Popen',return_value=process),self.assertRaises(ValueError):
                operator.bounded_run(['synthetic'],b'payload',1)
    def test_timeout_kills_without_retry_or_diagnostic(self):
        process=self.process();process.wait.side_effect=[operator.subprocess.TimeoutExpired('private',1),0]
        with patch.object(operator.subprocess,'Popen',return_value=process) as start,self.assertRaisesRegex(ValueError,'Operator relay stopped'):
            operator.bounded_run(['synthetic'],b'payload',1)
        start.assert_called_once();process.kill.assert_called_once()


if __name__=='__main__':unittest.main()
