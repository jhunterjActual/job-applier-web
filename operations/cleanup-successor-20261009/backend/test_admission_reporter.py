"""Synthetic fresh-observation/secret-boundary tests; no network or host data."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
import subprocess
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import admission_reporter as reporter
import admission_operations as ops


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.release=Path('/opt/careertrellis/staging/releases')/('c'*40)
        self.config={'version':1,'source':self.release.name,'ping_url':'https://hc-ping.com/11111111-1111-4111-8111-111111111111'}
        self.raw=ops.encoded(self.config)
        self.stack.enter_context(patch.object(reporter,'CONFIG',self.root/'config.json'))
        self.stack.enter_context(patch.object(reporter.purge,'STATE',self.root))
        self.stack.enter_context(patch.object(reporter.purge,'state_lock',side_effect=nullcontext))
        self.stack.enter_context(patch.object(ops,'read_protected',return_value=self.raw))
        self.write=self.stack.enter_context(patch.object(ops,'state_write'))
        self.send=self.stack.enter_context(patch.object(reporter.healthchecks,'submit',return_value='accepted'))
        self.stack.enter_context(patch.object(reporter.time,'time',return_value=1000))

    def health(self,issues=(),checked=1000):
        return ops.encoded({'schema':reporter.contract.SCHEMA,'source':self.release.name,'checked_at':checked,
            'result':'alert' if issues else 'ok','issues':list(issues)})

    def test_each_attempt_observes_again_and_never_replays_healthy(self):
        with patch.object(reporter,'observe',side_effect=[(0,self.health()),(None,b'')]) as observe:
            self.assertEqual('healthy',reporter.report(self.release)['observation'])
            self.assertEqual('alert',reporter.report(self.release)['observation'])
        self.assertEqual(2,observe.call_count)
        self.assertEqual([(),reporter.contract.UNAVAILABLE],[c.args[1] for c in self.send.call_args_list])
        self.assertNotIn(self.config['ping_url'],repr(self.write.call_args_list))

    def test_stale_malformed_and_failed_status_never_recover(self):
        for code,raw in [(0,self.health(checked=579)),(0,b'private malformed'),(1,self.health()),(None,b'')]:
            with patch.object(reporter,'observe',return_value=(code,raw)):
                self.assertEqual('alert',reporter.report(self.release)['observation'])
            self.assertEqual(reporter.contract.UNAVAILABLE,self.send.call_args.args[1])

    def test_changed_configuration_refuses_delivery(self):
        with patch.object(ops,'read_protected',side_effect=[self.raw,self.raw+b' ']),patch.object(reporter,'observe',return_value=(0,self.health())):
            with self.assertRaises(ops.OperationError): reporter.report(self.release)
        self.send.assert_not_called(); self.write.assert_not_called()

    def test_ambiguous_delivery_has_no_automatic_retry(self):
        self.send.return_value='unconfirmed'
        with patch.object(reporter,'observe',return_value=(0,self.health())):
            self.assertEqual('unconfirmed',reporter.report(self.release)['outcome'])
        self.send.assert_called_once()
        self.assertEqual('unconfirmed',self.write.call_args.args[1]['provider_acceptance'])

    def test_settings_reject_other_hosts_extra_fields_and_revision(self):
        for changed in ({'version':True},{'source':'d'*40},{'ping_url':'https://example.com/private'},
                {'ping_url':self.config['ping_url']+'/fail'},{'extra':'private'}):
            with self.assertRaises(ops.OperationError): reporter.settings(ops.encoded({**self.config,**changed}),self.release.name)

    def test_status_command_is_fixed_and_output_read_bounded(self):
        def launch(argv,**kw):
            self.assertEqual(['/bin/bash',str(self.release/'deploy/scripts/manage-admission-purge.sh'),'status'],argv)
            self.assertTrue(kw['start_new_session']); self.assertNotIn('ping_url',kw['env'])
            kw['stdout'].write(b'x'*5000)
            return Mock(wait=Mock(return_value=0))
        with patch.object(reporter.subprocess,'Popen',side_effect=launch):
            code,raw=reporter.observe(self.release)
        self.assertEqual(0,code); self.assertEqual(4097,len(raw))
        process=Mock(pid=12345,wait=Mock(side_effect=[subprocess.TimeoutExpired('synthetic',12),-9]))
        with patch.object(reporter.subprocess,'Popen',return_value=process),patch.object(reporter.os,'killpg',create=True) as kill, \
                patch.object(reporter.signal,'SIGKILL',9,create=True):
            self.assertEqual((None,b''),reporter.observe(self.release))
        kill.assert_called_once_with(12345,9)

    @unittest.skipUnless(os.name=='posix','POSIX process groups')
    def test_timeout_terminates_real_synthetic_descendant(self):
        pidfile=self.root/'child-pid'
        code='import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)'
        real=subprocess.Popen; parents=[]
        def launch(argv,**kw):
            parent=real([sys.executable,'-c',code,str(pidfile)],**kw); parents.append(parent)
            return parent
        try:
            with patch.object(reporter.subprocess,'Popen',side_effect=launch),patch.object(reporter,'STATUS_TIMEOUT',1):
                self.assertEqual((None,b''),reporter.observe(self.release))
            self.assertIsNotNone(parents[0].poll())
            child=int(pidfile.read_text())
            stat=Path('/proc')/str(child)/'stat'
            self.assertTrue(not stat.exists() or stat.read_text().split()[2]=='Z')
        finally:
            for parent in parents:
                if parent.poll() is None:
                    os.killpg(parent.pid,reporter.signal.SIGKILL); parent.wait(timeout=2)
