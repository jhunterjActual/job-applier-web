"""Pure collection regression: no systemd, process, or host operations."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

def load(old=False):
    source=Path(__file__).with_name('prepare_release.py').read_text(encoding='utf8')
    tree=ast.parse(source)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='collect_empty_unit')
    source=ast.get_source_segment(source,node)
    if old:source=source.replace('if inactive and not gone:', 'if inactive:')
    def need(value):
        if not value:raise ValueError('refused')
    baseline={'invocation_id':'a'*32}
    ns={'need':need,'time':SimpleNamespace(monotonic=lambda:0,sleep=lambda _:None),
        'subprocess':SimpleNamespace(DEVNULL=-3,run=Mock(return_value=SimpleNamespace(returncode=0))),
        'execution_terminal':lambda *args:baseline,
        'cgroup_state':lambda *args:{'processes':0,'populated':0}}
    exec(compile(source,'<collection-regression>','exec'),ns)
    return ns,baseline

class CollectionTests(unittest.TestCase):
    def run_case(self,after,old=False,client_exit=0):
        ns,proof=load(old);ns['unit_state']=Mock(side_effect=[{},after])
        client=SimpleNamespace(pid=123,wait=Mock(return_value=client_exit))
        return ns['collect_empty_unit']('fixed-unit',proof,client)
    def test_collected_notfound_inactive_blank_invocation_accepted(self):
        after={'LoadState':'not-found','ActiveState':'inactive','SubState':'dead','InvocationID':''}
        self.assertEqual(self.run_case(after)['client_exit'],0)
        with self.assertRaises(ValueError):self.run_case(after,old=True)
    def test_loaded_inactive_requires_exact_invocation(self):
        after={'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','Result':'success',
               'ExecMainStatus':'0','InvocationID':'a'*32}
        self.run_case(after)
        for key,value in [('InvocationID',''),('Result','failed'),('ExecMainStatus','1'),('MainPID','55'),('Job','queued')]:
            with self.subTest(key=key),self.assertRaises(ValueError):self.run_case(dict(after,**{key:value}))
    def test_notfound_still_refuses_pids_jobs_client_failure(self):
        for changes in [{'MainPID':'1'},{'ControlPID':'2'},{'Job':'queued'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.run_case(dict(LoadState='not-found',**changes))
        with self.assertRaises(ValueError):self.run_case({'LoadState':'not-found'},client_exit=1)

if __name__=='__main__':unittest.main()
