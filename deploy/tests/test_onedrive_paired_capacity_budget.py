"""Offline closed-request timeout, deadline and actual-allocation routing."""
import copy
import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from deploy.paired_maintenance import client,db_control as d,entry,ssh_gate
from deploy.paired_maintenance.common import canonical


def request():return {'schema':d.SCHEMA,'transaction':'a'*32,'policy_sha256':'b'*64,
    'operation':d.RETENTION_CAPACITY,'payload':{'stage':d.RETENTION_PRE_GROWTH,'retention_policy_sha256':'c'*64}}


class Tests(unittest.TestCase):
    def test_only_exact_valid_purpose_request_gets_210_225_budgets(self):
        value=request();self.assertIs(client.pre_growth_capacity_transport_ready(value),True)
        self.assertEqual(client.request_timeout(value['operation'],request=value),225)
        self.assertEqual(client.request_timeout(value['operation'],request=value,endpoint=True),210)
        self.assertEqual(client.request_timeout(value['operation']),35)
        for change in ('extra','stage','operation','zero'):
            row=copy.deepcopy(value)
            if change=='extra':row['payload']['timeout']=600
            elif change=='stage':row['payload']['stage']='hydrate'
            elif change=='operation':row['operation']=d.RETENTION_PREFLIGHT
            else:row['transaction']='0'*32
            self.assertIs(client.pre_growth_capacity_transport_ready(row),False)
            self.assertEqual(client.request_timeout(row['operation'],request=row),35)
        self.assertEqual(client.request_timeout('retention_activation_prepare'),195)

    def test_client_and_root_gate_use_full_closed_request_once(self):
        value=request();answer={'schema':client.RESPONSE_SCHEMA,'transaction':'a'*32,'policy_sha256':'b'*64,'payload':{'fixture':True}}
        completed=SimpleNamespace(returncode=0,stdout=canonical(answer))
        with patch.object(client,'_trusted_file'),patch.object(client.subprocess,'run',return_value=completed) as run:
            self.assertEqual(client.Client(SimpleNamespace(digest='b'*64),'a'*32).call(value['operation'],value['payload']),{'fixture':True})
            self.assertEqual(run.call_args.kwargs['timeout'],225);self.assertEqual(run.call_count,1)
        runner=Mock(return_value=completed)
        ssh_gate.run({'SSH_ORIGINAL_COMMAND':client.COMMAND},canonical(value),runner)
        self.assertEqual(runner.call_args.kwargs['timeout'],210);self.assertEqual(runner.call_count,1)
        runner=Mock(side_effect=TimeoutError())
        ssh_gate.run({'SSH_ORIGINAL_COMMAND':client.COMMAND},canonical(value),runner)
        self.assertEqual(runner.call_count,1)

    def fixture(self,capability=True):
        value=request();bindings={'paired_policy_sha256':'b'*64,'standalone_policy_sha256':'c'*64}
        context=SimpleNamespace(role='db',authenticated=True,bindings=bindings)
        policy=SimpleNamespace(digest='c'*64)
        selector=SimpleNamespace(role='db',digest='e'*64,bindings=bindings)
        facts={'quota':{'total':100000,'free':90000,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()},'namespace_bytes':100,'account_scope':'fixture',
            'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'baseline_bindings':bindings}
        events=[]
        provider=SimpleNamespace(source_allocation=Mock(side_effect=lambda:(events.append('source') or (100,200))),
            collect=Mock(side_effect=lambda:(events.append('census') or SimpleNamespace(authenticated=True,value=facts))),close=Mock())
        guard=SimpleNamespace(NativeProvider=Mock(return_value=provider),_namespace_owners=Mock(),
            native_history_snapshot=Mock(return_value={'baseline_bindings':bindings,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}),validate_peer_history=Mock())
        activation=SimpleNamespace(require_activated=Mock(return_value=selector))
        loader=SimpleNamespace(_load={'guard':guard,'activation':activation}.__getitem__)
        @contextmanager
        def repository_lock():yield 17,(1,2)
        modules={'db_control':d,'client':SimpleNamespace(pre_growth_capacity_transport_ready=Mock(return_value=capability)),
            'provider':SimpleNamespace(repository_lock=Mock(side_effect=repository_lock))}
        return value,policy,context,loader,modules,guard,provider,events

    def test_same_owned_fd_and_one_absolute_180_deadline_for_all_provider_reads(self):
        value,policy,context,loader,modules,guard,provider,events=self.fixture()
        with patch.object(entry,'_module',side_effect=modules.__getitem__),patch.object(entry.time,'monotonic',return_value=1000):
            result=entry._pre_growth_capacity(loader,value,policy,context)
        guard.NativeProvider.assert_called_once_with(17,operation_deadline=1180)
        self.assertEqual(events,['source','census']);provider.close.assert_called_once_with()
        self.assertEqual(result['allocation'],{'source_bytes':100,'candidate_bytes':200,'namespace_new_bytes':200})
        self.assertEqual(result['activation_sha256'],'e'*64)
        self.assertEqual(guard.native_history_snapshot.call_count,2)
        self.assertEqual(guard._namespace_owners.call_args.args[1],17)

    def test_non_exact_transport_callback_and_expired_deadline_refuse_before_provider(self):
        for capability in (False,1,{},None):
            value,policy,context,loader,modules,guard,provider,events=self.fixture(capability)
            with patch.object(entry,'_module',side_effect=modules.__getitem__),self.assertRaises(entry.EntryRefused):
                entry._pre_growth_capacity(loader,value,policy,context)
            modules['provider'].repository_lock.assert_not_called();guard.NativeProvider.assert_not_called()
        value,policy,context,loader,modules,guard,provider,events=self.fixture()
        with patch.object(entry,'_module',side_effect=modules.__getitem__),patch.object(entry.time,'monotonic',side_effect=[1000,1180]),self.assertRaises(entry.EntryRefused):
            entry._pre_growth_capacity(loader,value,policy,context)
        guard.NativeProvider.assert_not_called()

    def test_late_collection_and_stale_quota_refuse_without_retry(self):
        value,policy,context,loader,modules,guard,provider,events=self.fixture()
        with patch.object(entry,'_module',side_effect=modules.__getitem__),patch.object(entry.time,'monotonic',side_effect=[1000,1000,1181]),self.assertRaises(entry.EntryRefused):
            entry._pre_growth_capacity(loader,value,policy,context)
        self.assertEqual(events,['source','census']);provider.close.assert_called_once_with()
        value,policy,context,loader,modules,guard,provider,events=self.fixture()
        original=provider.collect.side_effect
        def stale():
            result=original();result.value['quota']['observed_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=121)).isoformat()
            return result
        provider.collect.side_effect=stale
        with patch.object(entry,'_module',side_effect=modules.__getitem__),patch.object(entry.time,'monotonic',return_value=1000),self.assertRaises(entry.EntryRefused):
            entry._pre_growth_capacity(loader,value,policy,context)
        self.assertEqual(events,['source','census']);provider.close.assert_called_once_with()


if __name__=='__main__':unittest.main()
