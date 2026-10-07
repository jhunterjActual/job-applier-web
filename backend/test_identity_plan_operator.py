"""Synthetic guided packet input; no installed files, real TTY or provider."""
from contextlib import ExitStack
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import admission_operations as ops
import identity_plan_operator as operator
import identity_plan_packet as packet
from test_identity_plan_packet import fixture


class OperatorTests(unittest.TestCase):
    def test_explicit_0021_authoring_keeps_initial_pairs_and_policy(self):
        replies=self.replies(); replies[-2]='20260929_0021'
        with patch.object(operator.handoff,'validate_files',return_value=self.provider()):
            value=packet.validate(operator.build(self.initial(),lambda _:replies.pop(0)))
        self.assertEqual(1,value['version'])
        for name in ('bind','switch'):
            activation=value[name]['activation']
            self.assertEqual('20260929_0021',activation['schema'])
            self.assertEqual((25,600,3600),(activation['cap'],activation['ttl'],activation['session_seconds']))
        self.assertNotEqual(value['bind']['provider']['operation'],value['switch']['provider']['operation'])

    def test_explicit_0016_authoring(self):
        replies=self.replies(); replies[-2]='20260915_0016'
        with patch.object(operator.handoff,'validate_files',return_value=self.provider()):
            value=packet.validate(operator.build(self.initial(),lambda _:replies.pop(0)))
        self.assertEqual('20260915_0016',value['bind']['activation']['schema'])
        self.assertEqual('20260915_0016',value['switch']['activation']['schema'])

    def initial(self):
        return {'application':'a'*40,'operations':'c'*40,'files':{},'units':{},'boot':'boot'}

    def replies(self):
        return ['VISIBLE','a'*40,'c'*40,'b'*40,'synthetic','d'*64,'e'*64,
            'user_synthetic_target','0',str(uuid4()),str(uuid4()),'1',str(uuid4()),'0',
            'VERIFIED','AUTHORING-AUTHORIZED','20260909_0015','SAVE']

    def provider(self):
        return (SimpleNamespace(binding='d'*64,generation='synthetic-source'),
            {'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID':'user_synthetic_source'}, {})

    def test_authoring_derives_exact_pairs_and_preserves_approved_policy(self):
        with patch.object(operator.handoff,'validate_files',return_value=self.provider()):
            answers=iter(self.replies())
            value=packet.validate(operator.build(self.initial(),lambda _:next(answers)))
        self.assertEqual(value['version'],1)
        self.assertEqual(value['switch']['provider']['expected_generation'],2)
        self.assertEqual(value['switch']['activation']['session_seconds'],3600)
        self.assertEqual(value['bind']['activation']['realm'],'synthetic-source')
        self.assertEqual(value['switch']['activation']['realm'],'synthetic')
        self.assertNotIn('user_synthetic',ops.encoded(value).decode())

    def test_no_approval_or_invalid_numeric_or_duplicate_uuid_cannot_author(self):
        for index,replacement in ((0,'NO'),(9,'-1'),(12,'26'),(15,'NO'),(16,'NO')):
            replies=self.replies(); replies[index]=replacement
            with self.subTest(index=index),patch.object(operator.handoff,'validate_files',return_value=self.provider()),self.assertRaises(ops.OperationError):
                operator.build(self.initial(),lambda _:replies.pop(0))

    def environment(self,stack,action='enter',previous=None):
        stack.enter_context(patch.object(operator,'preflight',return_value=self.initial()))
        stack.enter_context(patch.object(operator.store,'exists',return_value=previous is not None))
        stack.enter_context(patch.object(operator.store,'load',return_value=(previous,{})))
        terminal=stack.enter_context(patch.object(operator,'SecureTerminal')).return_value.__enter__.return_value
        output=stack.enter_context(patch('sys.stdout',new_callable=io.StringIO))
        return terminal,output

    def test_cancel_does_not_archive_and_error_is_fixed(self):
        with ExitStack() as stack:
            terminal,output=self.environment(stack)
            terminal.ask.side_effect=InterruptedError('private-input')
            register=stack.enter_context(patch.object(operator.store,'register'))
            with self.assertRaises(ops.OperationError) as caught: operator.run('enter',Path('unused'))
            self.assertEqual(str(caught.exception),ops.ERROR)
            register.assert_not_called(); self.assertEqual(output.getvalue(),'')

    def test_drift_after_input_does_not_archive(self):
        with ExitStack() as stack:
            _,output=self.environment(stack)
            stack.enter_context(patch.object(operator,'build',return_value=ops.encoded(fixture())))
            stack.enter_context(patch.object(operator,'preflight',side_effect=[self.initial(),{**self.initial(),'boot':'changed'}]))
            register=stack.enter_context(patch.object(operator.store,'register'))
            with self.assertRaises(ops.OperationError): operator.run('enter',Path('unused'))
            register.assert_not_called(); self.assertEqual(output.getvalue(),'')

    def test_enter_only_archives_fixed_output(self):
        with ExitStack() as stack:
            _,output=self.environment(stack)
            raw=ops.encoded(fixture())
            stack.enter_context(patch.object(operator,'build',return_value=raw))
            register=stack.enter_context(patch.object(operator.store,'register',return_value='private-digest'))
            select=stack.enter_context(patch.object(operator.store,'select'))
            self.assertEqual(operator.run('enter',Path('unused')),0)
            register.assert_called_once_with(raw); select.assert_not_called()
            self.assertEqual(ops.strict_json(output.getvalue()),{'outcome':'packet_archived_not_selected'})

    def test_selection_passes_pre_prompt_snapshot_to_cas(self):
        value=fixture(); operation=value['switch']['provider']['operation']; raw=ops.encoded(value)
        for previous in (None,b'private-initial-pointer'):
            with self.subTest(previous=previous),ExitStack() as stack:
                terminal,output=self.environment(stack,previous=previous)
                terminal.ask.side_effect=['VISIBLE',operation,'SELECT']
                stack.enter_context(patch.object(operator,'find_packet',return_value=('f'*64,raw)))
                stack.enter_context(patch.object(operator.store,'decode',return_value=({'digest':'f'*64},{})))
                stack.enter_context(patch.object(ops,'read_protected',return_value=raw))
                select=stack.enter_context(patch.object(operator.store,'select'))
                operator.run('select',Path('unused'))
                select.assert_called_once_with('f'*64,operation,previous=previous)
                self.assertEqual(ops.strict_json(output.getvalue()),{'outcome':'plan_selected_not_activated'})

    def test_selection_cas_failure_never_reports_success(self):
        with ExitStack() as stack:
            terminal,output=self.environment(stack)
            value=fixture(); operation=value['bind']['provider']['operation']
            terminal.ask.side_effect=['VISIBLE',operation,'SELECT']
            stack.enter_context(patch.object(operator,'find_packet',return_value=('f'*64,ops.encoded(value))))
            stack.enter_context(patch.object(operator.store,'select',side_effect=ValueError('private-pointer')))
            with self.assertRaises(ops.OperationError) as caught: operator.run('select',Path('unused'))
            self.assertEqual(str(caught.exception),ops.ERROR); self.assertEqual(output.getvalue(),'')

    def test_status_does_not_prompt_and_has_no_private_output(self):
        for previous in (None,b'private-pointer'):
            with ExitStack() as stack:
                terminal,output=self.environment(stack,previous=previous)
                operator.run('status',Path('unused'))
                terminal.ask.assert_not_called()
                self.assertNotIn('private',output.getvalue())

    def test_initial_archive_ambiguity_refused(self):
        value=fixture(); other=fixture()
        other['bind']=value['bind']
        rows=[ops.encoded(v) for v in (value,other)]
        paths=[operator.store.archive(hashlib.sha256(raw).hexdigest()) for raw in rows]
        state=SimpleNamespace(glob=lambda _:iter(paths))
        with patch.object(ops,'STATE',state),patch.object(operator.store,'archive',side_effect=lambda digest:Path('/etc/careertrellis/admission-operations')/('identity-plan-'+digest+'.json')),patch.object(ops,'read_protected',side_effect=rows):
            with self.assertRaises(ops.OperationError): operator.find_packet(value['bind']['provider']['operation'])
