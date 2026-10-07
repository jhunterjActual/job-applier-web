"""Actual concurrent protected protocol + operator pipeline, synthetic SSH/DB.

All five wrapper sources are verified from a temporary bundle. The injected
SSH runner routes only protocol wire messages; it never executes a subprocess,
reads credentials, or contacts a database/provider.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import owner_recovery_reserve_operator as operator
import owner_recovery_reserve_relay as protocol

@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid()==0,
    'native Linux root-owned atomic publication contract')
class ConcurrentRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='reserve-relay-integration-',dir='/root')
        self.base=Path(self.temp.name);os.chmod(self.base,0o700)
        reserve=self.base/'reserve';reserve.mkdir(mode=0o700)
        self.convergence=reserve/'convergence';self.convergence.mkdir(mode=0o700)
        self.root_patch=patch.object(protocol,'ROOT',self.convergence);self.root_patch.start()
        self.credential_patch=patch.object(protocol.recovery,'Store',side_effect=AssertionError('credential access forbidden'))
        self.credential_store=self.credential_patch.start()
        self.bundle=self.base/('c'*40);(self.bundle/'backend').mkdir(parents=True)
        wrappers={};sources=Path(operator.__file__).parent
        for name in operator.MODULES:
            raw=(sources/(name+'.py')).read_bytes()
            (self.bundle/'backend'/(name+'.py')).write_bytes(raw)
            wrappers['backend/'+name+'.py']=operator.digest(raw)
        manifest={'version':1,'operations':operator.OPS,'wrapper':wrappers,
            'library':{name:'a'*64 for name in ('backend/admission_operations.py',
                'backend/identity_plan_store.py','backend/identity_plan_packet.py')}}
        raw=operator.encoded(manifest)+b'\n';(self.bundle/'source-manifest.json').write_bytes(raw)
        self.pin=operator.digest(raw);self.clock=100.0;self.wake=threading.Event();self.threads=[]
        self.selection=b'{"synthetic":"exact private selection"}'
        self.scope=dict(operation='11111111-1111-4111-8111-111111111111',
            selection_sha256=operator.digest(self.selection),activation_sha256='b'*64,
            enrollment_sha256='e'*64,closure_sha256=self.pin,actions=['consume'])
        self.context={key:value for key,value in self.scope.items() if key not in ('actions','selection_sha256')}
        self.context.update(action='consume',stage='pre',selection=self.selection)
        self.journal=self.base/'journal';self.journal.mkdir(mode=0o700)
        self.calls=[];self.mismatch=False;self.lost_ack=False
        self.relay=operator.Relay(bundle_directory=self.bundle,manifest_sha256=self.pin,scope=self.scope,
            journal_directory=self.journal,runner=self.runner,now=lambda:self.clock)
    def tearDown(self):
        self.clock+=100;self.wake.set()
        for thread in self.threads:thread.join(timeout=3)
        self.credential_store.assert_not_called();self.credential_patch.stop()
        self.root_patch.stop();self.temp.cleanup()
        self.assertTrue(all(not thread.is_alive() for thread in self.threads))
    def runner(self,args,payload,timeout):
        envelope=json.loads(payload);request=envelope['request'];role=args[-2]
        self.assertLessEqual(timeout,15);self.assertNotIn(self.selection.hex(),' '.join(args))
        if role==operator.HOSTS['db']:
            self.calls.append('db-observe')
            result={'selection_hex':self.selection.hex(),'selection_sha256':operator.digest(self.selection),
                'activation_sha256':'d'*64 if self.mismatch else self.scope['activation_sha256']}
        else:
            self.assertEqual(operator.HOSTS['web'],role)
            self.assertEqual(self.pin,request['manifest_sha256'])
            action=request['argv'][0];self.calls.append(action)
            if action=='relay-peek':result=protocol.peek(now=lambda:self.clock)
            elif action=='relay-status':result=protocol.inspect(request['argv'][1],now=lambda:self.clock)
            elif action=='relay-publish':
                body=json.loads(base64.b64decode(request['input'],validate=True))
                result=protocol.publish(body['request_id'],body['observation'],now=lambda:self.clock)
                self.wake.set()
                if self.lost_ack:raise TimeoutError('synthetic lost publish acknowledgement')
            else:self.fail('unexpected wire action')
        return operator.encoded(result)
    def start_request(self):
        result=[];errors=[]
        def wait(seconds):self.wake.wait(min(seconds,0.05));self.wake.clear()
        def worker():
            try:result.append(protocol.request_observation(self.context,now=lambda:self.clock,wait=wait))
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=worker,daemon=True);self.threads.append(thread);thread.start()
        limit=time.monotonic()+3
        while time.monotonic()<limit:
            observed=protocol.peek(now=lambda:self.clock)
            if observed['status']=='pending':return thread,result,errors,observed['request']['id']
            if errors:self.fail('request failed before publication')
            time.sleep(0.01)
        self.fail('request was not published within bounded fixture wait')
    def test_real_handshake_twice_uses_distinct_nonces_and_private_journal(self):
        identifiers=[]
        for stage in ('pre','post'):
            self.context['stage']=stage
            thread,result,errors,request_id=self.start_request();identifiers.append(request_id)
            self.assertEqual('published',self.relay.once());thread.join(timeout=3)
            self.assertFalse(thread.is_alive());self.assertEqual([],errors);self.assertEqual([self.selection],result)
            self.assertEqual('answered',protocol.inspect(request_id,now=lambda:self.clock)['status'])
        self.assertNotEqual(*identifiers)
        self.assertEqual(2,self.calls.count('db-observe'));self.assertEqual(2,self.calls.count('relay-publish'))
        for path in self.journal.iterdir():
            self.assertNotIn(self.selection,path.read_bytes());self.assertNotIn(self.selection.hex().encode(),path.read_bytes())
    def test_database_mismatch_preserves_claim_and_reconciles_expired_without_publish(self):
        self.mismatch=True;thread,result,errors,request_id=self.start_request()
        with self.assertRaises(ValueError):self.relay.once()
        self.clock+=31;self.wake.set();thread.join(timeout=3)
        self.assertFalse(thread.is_alive());self.assertEqual([],result);self.assertEqual(1,len(errors))
        self.assertNotIn('relay-publish',self.calls)
        before=list(self.calls)
        self.assertEqual({'status':'expired','authorization':False},self.relay.reconcile(request_id))
        self.assertEqual(before+['relay-status'],self.calls)
        self.assertTrue(self.relay.journal.path(request_id,'claim').exists())
        self.assertFalse(protocol.path('response',request_id).exists())
    def test_lost_publish_ack_reconciles_actual_immutable_answer_without_retry(self):
        self.lost_ack=True;thread,result,errors,request_id=self.start_request()
        with self.assertRaises(ValueError) as caught:self.relay.once()
        self.assertEqual(operator.ERROR,str(caught.exception))
        thread.join(timeout=3);self.assertFalse(thread.is_alive())
        self.assertEqual([],errors);self.assertEqual([self.selection],result)
        original=protocol.path('response',request_id).read_bytes();before=list(self.calls)
        self.assertEqual({'status':'answered','authorization':False},self.relay.reconcile(request_id))
        self.assertEqual(before+['relay-status'],self.calls)
        self.assertEqual(original,protocol.path('response',request_id).read_bytes())
        self.assertEqual(1,self.calls.count('relay-publish'))

if __name__=='__main__':unittest.main()
