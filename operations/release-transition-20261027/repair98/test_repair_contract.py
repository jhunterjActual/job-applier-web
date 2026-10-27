"""Pure closed-contract/durable-ledger/custody negatives; no host calls."""
import ast
import copy
import datetime as dt
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import repair_contract as c

class ContractTests(unittest.TestCase):
    def test_durable_create_fsyncs_parent_before_readback(self):
        events=[];value={'public':True};raw=c.canonical(value)
        class Stream(io.BytesIO):
            def fileno(self):return 17
        stream=Stream()
        with patch.object(c,'parents'),patch.object(c.os,'O_NOFOLLOW',1,create=True),patch.object(c.os,'open',return_value=17),\
            patch.object(c.os,'dup',return_value=18),patch.object(c.os,'fdopen',return_value=stream),patch.object(c.os,'fchmod',create=True),\
            patch.object(c.os,'fsync',side_effect=lambda fd:events.append('file')),patch.object(c.os,'close'),\
            patch.object(c,'sync_dir',side_effect=lambda path:events.append('parent')),\
            patch.object(c,'read',side_effect=lambda path:events.append('readback') or raw):
            c.create(Path('/fixed/new.json'),value)
        self.assertEqual(events,['file','parent','readback'])
    def test_coordinator_unknown_client_custody_stays_inside_lock_finally(self):
        source=Path('repair_coordinator.py').read_text();tree=ast.parse(source)
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        lock=next(n for n in ast.walk(main) if isinstance(n,ast.With) and 'ns' in ast.get_source_segment(source,n.items[0].context_expr))
        self.assertIsInstance(lock.body[0],ast.Try)
        self.assertIn('hold_owned(client)',ast.get_source_segment(source,lock.body[0].finalbody[0]))
        hold=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='hold_owned')
        client=SimpleNamespace(poll=Mock(side_effect=[None,0]))
        states=iter([{'LoadState':'loaded','Job':''},{'LoadState':'not-found','Job':''}])
        ns={'UNIT':'fixed','unit_state':lambda unit:next(states),'cgroup_state':lambda *a:{'processes':0,'populated':0}}
        exec(compile(ast.Module(body=[hold],type_ignores=[]),'<actual-hold>','exec'),ns)
        with patch('time.sleep'):ns['hold_owned'](client)
        self.assertEqual(client.poll.call_count,2)
    def test_seal_never_asserts_running_owner_exit_and_rechecks_deadline(self):
        source=Path('repair_seal.py').read_text()
        self.assertIn("owner_terminal['owner_self_exit_claim'] is False",source)
        self.assertLess(source.index('absent_original(owner_terminal'),source.index("record('postterminal-owner-proof.json'"))
        self.assertLess(source.index('validate_owner_terminal('),source.index("create(DOMAIN/'accepted.json'"))
        self.assertIn("need(boot()==current_boot and utc()<dt.datetime.fromisoformat(plan['expires_at']))",source)

if __name__=='__main__':unittest.main()
