"""Native synthetic relay filesystem protocol; no SSH, provider or credentials."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import owner_recovery_reserve_relay as relay

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'native root-owned protected filesystem')
class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='reserve-relay-test-',dir='/root')
        parent=Path(self.temp.name);os.chmod(parent,0o700)
        self.root=parent/'convergence';self.root.mkdir(mode=0o700)
        self.patch=patch.object(relay,'ROOT',self.root);self.patch.start();self.clock=100.0
        self.context=dict(action='consume',stage='pre',operation=str(uuid4()),enrollment_sha256='a'*64,
            selection=b'synthetic-selection',activation_sha256='b'*64,closure_sha256='c'*64)
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def observation(self,req):
        value={key:req['context'][key] for key in ('selection_hex','selection_sha256','activation_sha256',
            'closure_sha256','enrollment_sha256','operation')}
        value['request_sha256']=relay.digest(relay.recovery.encoded(req));return value
    def respond(self,seconds):
        self.clock+=seconds;req=relay.peek(now=lambda:self.clock)['request']
        relay.publish(req['id'],self.observation(req),now=lambda:self.clock)
    def request(self,wait=None):
        return relay.request_observation(self.context,now=lambda:self.clock,wait=wait or self.respond,monotonic=lambda:self.clock)
    def write_request(self,created=100):
        req=dict(version=1,id=str(uuid4()),created=created,expires=created+30,context=relay.wire_context(self.context))
        with relay.locked():relay.exclusive(relay.path('request',req['id']),req)
        return req
    def test_success_exact_selection_and_distinct_nonce_each_check(self):
        self.assertEqual(self.context['selection'],self.request());first=set(relay.inventory())
        self.assertEqual(self.context['selection'],self.request());second=set(relay.inventory())
        self.assertEqual(1,len(first));self.assertEqual(2,len(second));self.assertTrue(first<second)
        self.assertEqual({'status':'idle'},relay.peek(now=lambda:self.clock))
    def test_timeout_retains_request_and_never_deletes(self):
        def timeout(seconds):self.clock+=31
        with self.assertRaises(ValueError):self.request(wait=timeout)
        ids=relay.inventory();self.assertEqual(1,len(ids))
        result=relay.inspect(ids[0],now=lambda:self.clock)
        self.assertEqual('expired',result['status']);self.assertFalse(result['authorization'])
    def test_wrong_closure_binding_selection_or_operation_refused(self):
        req=self.write_request()
        for key in ('request_sha256','selection_hex','selection_sha256','activation_sha256','closure_sha256',
            'enrollment_sha256','operation'):
            value=self.observation(req);value[key]='incorrect'
            with self.assertRaises(ValueError):relay.publish(req['id'],value,now=lambda:self.clock)
        self.assertFalse(relay.path('response',req['id']).exists())
    def test_old_answer_never_matches_new_challenge(self):
        old=self.write_request();new=self.write_request()
        with self.assertRaises(ValueError):relay.publish(new['id'],self.observation(old),now=lambda:self.clock)
    def test_publish_exclusive_lost_ack_inspection_is_read_only(self):
        req=self.write_request();value=self.observation(req)
        relay.publish(req['id'],value,now=lambda:self.clock)
        raw=relay.path('response',req['id']).read_bytes()
        with self.assertRaises(ValueError):relay.publish(req['id'],value,now=lambda:self.clock)
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        result=relay.inspect(req['id'],now=lambda:self.clock)
        self.assertEqual('answered',result['status']);self.assertFalse(result['authorization'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(raw,relay.path('response',req['id']).read_bytes())
    def test_expiry_during_durable_publish_retains_answer_but_fails(self):
        req=self.write_request();times=iter((100,131))
        with self.assertRaises(ValueError):relay.publish(req['id'],self.observation(req),now=lambda:next(times))
        result=relay.inspect(req['id'],now=lambda:131)
        self.assertEqual('answered',result['status']);self.assertFalse(result['currently_fresh'])
    def test_peek_selects_newest_unanswered_request(self):
        old=self.write_request(99);new=self.write_request(100)
        self.assertEqual(new['id'],relay.peek(now=lambda:101)['request']['id'])
        relay.publish(new['id'],self.observation(new),now=lambda:101)
        self.assertEqual(old['id'],relay.peek(now=lambda:101)['request']['id'])
    def test_protected_request_symlink_and_hardlink_refused(self):
        req=self.write_request();path=relay.path('request',req['id']);other=self.root/'other'
        path.rename(other);path.symlink_to(other)
        with self.assertRaises((ValueError,OSError)):relay.peek(now=lambda:100)
        path.unlink();os.link(other,path)
        with self.assertRaises(ValueError):relay.peek(now=lambda:100)
    def test_absent_root_not_created(self):
        missing=self.root/'absent'
        with patch.object(relay,'ROOT',missing),self.assertRaises(FileNotFoundError):relay.peek(now=lambda:100)
        self.assertFalse(missing.exists())
    def test_reserve_parent_requires_exact_0700(self):
        os.chmod(self.root.parent,0o755)
        try:
            with self.assertRaises(ValueError):relay.peek(now=lambda:100)
            self.assertEqual([],list(self.root.iterdir()))
        finally:os.chmod(self.root.parent,0o700)
    def test_bounded_inventory_prevents_more_requests(self):
        self.write_request()
        with patch.object(relay,'LIMIT',1),self.assertRaises(ValueError):self.request()
        self.assertEqual(1,len(relay.inventory()))
    def test_clock_rollback_refuses(self):
        def rollback(seconds):self.clock-=1
        with self.assertRaises(ValueError):self.request(wait=rollback)
    def test_monotonic_deadline_bounds_frozen_wall_clock(self):
        ticks=iter((0,31))
        with self.assertRaises(ValueError):relay.request_observation(self.context,now=lambda:100,
            wait=lambda seconds:self.fail('should expire before wait'),monotonic=lambda:next(ticks))
    def test_reader_interleaving_at_atomic_publication_has_single_link(self):
        rename=relay._rename_noreplace;observed=[]
        def interleave(source,target):
            self.assertEqual(1,source.stat().st_nlink)
            self.assertFalse(target.exists())
            rename(source,target)
            self.assertEqual(1,target.stat().st_nlink)
            # Force a reader precisely after publication, before writer fsync
            # and return. Old link/unlink publication failed this boundary.
            if target.name.startswith('request-'):
                value=relay.peek(now=lambda:100)
                observed.append(value['request']['id'])
            else:
                request_id=target.name[9:-5]
                self.assertEqual('answered',relay.inspect(request_id,now=lambda:100)['status'])
                observed.append(request_id)
        with patch.object(relay,'_rename_noreplace',side_effect=interleave):
            req=self.write_request()
            relay.publish(req['id'],self.observation(req),now=lambda:100)
        self.assertEqual([req['id'],req['id']],observed)
    def test_atomic_publish_never_overwrites_existing_and_retains_temp(self):
        req=self.write_request();location=relay.path('request',req['id']);original=location.read_bytes()
        with self.assertRaises(FileExistsError):relay.exclusive(location,{**req,'expires':120})
        self.assertEqual(original,location.read_bytes());self.assertEqual(1,location.stat().st_nlink)
        self.assertEqual(1,len(list(self.root.glob('.pending-*'))))
    def test_unsupported_atomic_publish_retains_evidence_without_fallback(self):
        from types import SimpleNamespace
        request_id=str(uuid4());location=relay.path('request',request_id)
        with patch.object(relay.ctypes,'CDLL',return_value=SimpleNamespace()),self.assertRaises(ValueError):
            relay.exclusive(location,{'synthetic':'complete'})
        self.assertFalse(location.exists())
        temporary=list(self.root.glob('.pending-*'));self.assertEqual(1,len(temporary))
        self.assertEqual({'synthetic':'complete'},relay.read(temporary[0]))

class WireTests(unittest.TestCase):
    def test_strict_context_no_extra_or_secret_fields(self):
        with self.assertRaises(ValueError):relay.wire_context({'secret':'must not be accepted'})
    def test_identifier_canonical_only(self):
        self.assertFalse(relay.identifier('../escape'));self.assertFalse(relay.identifier(str(uuid4()).upper()))

if __name__=='__main__':unittest.main()
