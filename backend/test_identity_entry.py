"""Guided entry tests use synthetic scope and a synthetic-only native terminal."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops
import identity_entry as entry
import identity_operator as operator
from test_identity_evidence import packet


def answers(c):
    timestamp=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    values=['VISIBLE']+[str(c[k]) for k in ('account_id','incarnation','epoch','old_binding','old_subject','target_binding','target_subject')]
    values+=['VERIFIED',timestamp,'AUTHENTICATED',timestamp,'google','EMAIL-VERIFIED','none_enrolled','REVOKED',timestamp]
    if c['expected_role']=='owner': values+=['OWNER-AUTHORIZED',timestamp]
    return values+['SAVE']


class GuidedEntryTests(unittest.TestCase):
    def test_valid_scope_and_separate_owner_assertion(self):
        for owner in (False,True):
            c=packet()['context']
            if owner: c.update(expected_role='owner',owner_authorized=True)
            items=iter(answers(c)); raw=entry.build(c,lambda prompt:next(items))
            proof=ops.strict_json(raw)
            self.assertEqual(c,proof['context'])
            self.assertEqual(owner,proof['owner_approval'] is not None)
            self.assertEqual(2,proof['version'])
            self.assertEqual('google',proof['target']['authentication_method'])

    def test_wrong_scope_unconfirmed_proof_or_cancel_refuses(self):
        c=packet()['context']
        for index in (0,1,2,3,4,5,6,7,8,10,12,13,14,15,17):
            values=answers(c); values[index]='wrong'
            with self.assertRaises(Exception): entry.build(c,lambda prompt:values.pop(0))

    def test_raw_data_never_appears_in_prompts_or_error(self):
        c=packet()['context']; prompts=[]; values=answers(c); values[-1]='CANCEL'
        def ask(prompt): prompts.append(prompt); return values.pop(0)
        with self.assertRaises(ops.OperationError) as error: entry.build(c,ask)
        for subject in (c['old_subject'],c['target_subject'],c['incarnation']):
            self.assertNotIn(subject,str(error.exception)+' '.join(prompts))

    def test_no_prompt_until_preflight_and_no_db_transaction_during_input(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root=Path(directory); path=root/'input'; journal=root/'journal'; c=packet()['context']; active=[]
            stack.enter_context(patch.object(operator.barrier,'require_unblocked'))
            stack.enter_context(patch.object(operator,'locations',return_value=(path,journal)))
            stack.enter_context(patch.object(ops,'STATE',root))
            stack.enter_context(patch.object(ops,'protected_parents'))
            stack.enter_context(patch.object(Path,'lstat',return_value=Mock(st_mode=0o40700,st_uid=0)))
            stack.enter_context(patch.object(Path,'exists',return_value=False))
            stack.enter_context(patch.object(Path,'is_symlink',return_value=False))
            @contextmanager
            def connection(engine):
                active.append(True)
                try: yield Mock()
                finally: active.pop()
            stack.enter_context(patch.object(operator,'secure_connection',side_effect=connection))
            stack.enter_context(patch.object(operator,'setup'))
            check=stack.enter_context(patch.object(entry,'context',return_value=c))
            stack.enter_context(patch.object(ops,'host_locks',side_effect=lambda:ExitStack()))
            @contextmanager
            def terminal():
                self.assertFalse(active)
                values=iter(answers(c)); yield Mock(ask=lambda prompt:next(values))
            prompt=stack.enter_context(patch.object(entry,'SecureTerminal',side_effect=terminal))
            write=stack.enter_context(patch.object(ops,'write_protected'))
            self.assertEqual({'outcome':'manifest_saved_not_submitted'},entry.enter(c['operation'],Mock()))
            self.assertEqual(2,check.call_count); write.assert_called_once()
            write.reset_mock(); check.side_effect=[c,{**c,'epoch':c['epoch']+1}]
            with self.assertRaises(Exception): entry.enter(c['operation'],Mock())
            write.assert_not_called()
            write.reset_mock(); check.side_effect=ops.OperationError('synthetic unavailable'); prompt.reset_mock()
            with self.assertRaises(ops.OperationError): entry.enter(c['operation'],Mock())
            prompt.assert_not_called(); write.assert_not_called()

    def test_redirected_input_refuses_before_opening_tty(self):
        with patch.object(os,'isatty',return_value=False),patch.object(os,'open') as opened:
            with self.assertRaises(ops.OperationError):
                with entry.SecureTerminal(): pass
            opened.assert_not_called()


@unittest.skipUnless(os.name=='posix','Native Linux terminal fixture required')
class NativeTerminalTests(unittest.TestCase):
    def exercise(self,payload,expected):
        import fcntl
        import select
        import termios
        import time
        import signal
        master,slave=os.openpty(); pid=os.fork()
        if pid==0:
            try:
                os.close(master); os.setsid(); fcntl.ioctl(slave,termios.TIOCSCTTY,0)
                for fd in (0,1,2): os.dup2(slave,fd)
                if slave>2: os.close(slave)
                before=termios.tcgetattr(0); outcome='refused'
                handlers={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGHUP,signal.SIGTSTP,signal.SIGINT,signal.SIGTTIN,signal.SIGTTOU)}
                try:
                    with ExitStack() as stack:
                        if payload==b'INIT_FAIL': stack.enter_context(patch.object(entry.time,'monotonic',side_effect=RuntimeError('synthetic setup failure')))
                        if payload==b'BACKGROUND': stack.enter_context(patch.object(os,'tcgetpgrp',return_value=os.getpgrp()+1))
                        terminal=stack.enter_context(entry.SecureTerminal())
                        if payload==b'TIMEOUT': terminal.deadline=time.monotonic()-.1
                        outcome='accepted' if terminal.ask('Synthetic fixture')=='SYNTHETIC_ONLY' else 'wrong'
                except (Exception,KeyboardInterrupt): pass
                okay=outcome==expected and termios.tcgetattr(0)==before and all(signal.getsignal(s)==h for s,h in handlers.items())
                os._exit(0 if okay else 1)
            except BaseException: os._exit(2)
        os.close(slave); captured=b''; sent=False; status=None
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try: chunk=os.read(master,4096)
                    except OSError: chunk=b''
                    captured+=chunk
                    if b'(hidden): ' in captured and not sent:
                        sent=True
                        if payload==b'SIGTERM': os.kill(pid,signal.SIGTERM)
                        elif payload!=b'TIMEOUT': os.write(master,payload)
                done,status=os.waitpid(pid,os.WNOHANG)
                if done: break
            else:
                os.kill(pid,9); os.waitpid(pid,0); self.fail('synthetic terminal timed out')
            self.assertEqual(0,os.waitstatus_to_exitcode(status))
            self.assertNotIn(b'SYNTHETIC_ONLY',captured)
        finally: os.close(master)

    def test_hidden_input_and_terminal_restoration(self): self.exercise(b'SYNTHETIC_ONLY\n','accepted')
    def test_hidden_bracketed_paste_and_restoration(self): self.exercise(b'\x1b[200~SYNTHETIC_ONLY\x1b[201~\n','accepted')
    def test_multiline_bracketed_paste_refuses(self): self.exercise(b'\x1b[200~first\nsecond\x1b[201~\n','refused')
    def test_unmatched_paste_end_refuses(self): self.exercise(b'first\x1b[201~\n','refused')
    def test_nested_paste_start_refuses(self): self.exercise(b'\x1b[200~\x1b[200~value\n','refused')
    def test_missing_paste_end_refuses(self): self.exercise(b'\x1b[200~value\n','refused')
    def test_unrelated_escape_refuses(self): self.exercise(b'\x1b[31mvalue\n','refused')
    def test_oversized_input_restores_terminal(self): self.exercise(b'x'*257+b'\n','refused')
    def test_interrupt_restores_terminal(self): self.exercise(b'\x03','refused')
    def test_timeout_restores_terminal(self): self.exercise(b'TIMEOUT','refused')
    def test_termination_restores_terminal(self): self.exercise(b'SIGTERM','refused')
    def test_suspend_key_cancels_and_restores_terminal(self): self.exercise(b'\x1a','refused')
    def test_setup_failure_restores_terminal(self): self.exercise(b'INIT_FAIL','refused')
    def test_background_terminal_refuses(self): self.exercise(b'BACKGROUND','refused')
    def test_end_of_input_cancels_and_restores(self): self.exercise(b'\x04','refused')
