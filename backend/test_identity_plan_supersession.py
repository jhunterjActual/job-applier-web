"""Synthetic private-file supersession failures; real SQL covered in PG suite."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import admission_operations as ops
import identity_plan_packet as packet
import identity_plan_store as store
import identity_plan_supersession as supersession
import identity_operator as operator
import identity_web_handoff as handoff
import session_v2_transition as transition
from test_identity_web_handoff import HandoffTests


class SupersessionTests(HandoffTests):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(ops,'host_locks',side_effect=nullcontext))
        self.stack.enter_context(patch.object(operator,'secure_connection',side_effect=lambda e:nullcontext(Mock())) )
        self.stack.enter_context(patch.object(operator,'setup'))
        b={**self.p,'operation':str(uuid4()),'mode':'bind_current','target_binding':self.p['baseline_binding'],
            'transitions':[],'expected_generation':1}
        b['owner_proof']=transition.owner_proof(b['operation'],b['target_binding'],'user_synthetic_old')
        ba={**self.a,'operation':b['operation'],'provider_binding':b['target_binding'],'owner_proof':b['owner_proof']}
        self.original={'version':1,'bind':{'provider':b,'activation':ba},'switch':{'provider':self.p,'activation':self.a}}
        self.digest=store.register(ops.encoded(self.original))
        self.snapshot=store.select(self.digest,self.p['operation'],previous=None)
        self.source=Path('/opt/careertrellis/staging/releases')/self.p['operations']/'backend/admission_operations.py'

    def revised(self,c,old):
        value=deepcopy(old); value['version']=2
        value['switch']['provider'].update(expected_generation=4,transitions=[str(uuid4())],source_application=self.p['application'])
        return value

    def test_supersession_retains_original_and_requires_candidate_reentry(self):
        self.prepare()
        sealed=handoff.paths(self.p)[0]; before=sealed.read_bytes()
        with patch.object(supersession,'replacement',side_effect=self.revised):
            result=supersession.supersede(self.snapshot,self.source,None)
        self.assertEqual('uncommitted_plan_superseded_candidate_reentry_required',result['outcome'])
        current,pair=store.load()
        self.assertNotEqual(self.snapshot,current)
        self.assertEqual(self.p['operation'],pair['provider']['operation'])
        self.assertEqual(before,sealed.read_bytes())
        self.assertEqual(self.before,handoff.read_files())
        self.assertEqual(self.original,packet.validate(store.archive(self.digest).read_bytes()))
        self.db.assert_not_called()

    def test_database_rejection_never_changes_selection(self):
        self.prepare()
        with patch.object(supersession,'replacement',side_effect=ops.OperationError(ops.ERROR)):
            with self.assertRaises(ops.OperationError): supersession.supersede(self.snapshot,self.source,None)
        self.assertEqual(self.snapshot,store.load()[0])

    def test_lost_selection_ack_can_be_read_without_repeating_database_work(self):
        self.prepare(); write=ops.write_protected
        def lost(path,*args,**kwargs):
            write(path,*args,**kwargs)
            if path==store.location(): raise OSError('synthetic lost acknowledgement')
        with patch.object(supersession,'replacement',side_effect=self.revised),patch.object(ops,'write_protected',side_effect=lost):
            with self.assertRaises(OSError): supersession.supersede(self.snapshot,self.source,None)
        self.assertNotEqual(self.snapshot,store.load()[0])
        with self.assertRaises(ops.OperationError): supersession.supersede(self.snapshot,self.source,None)

    def test_changed_web_bytes_refuse_before_database_supersession(self):
        self.prepare()
        (self.root/'staging.env').write_text(self.before['staging.env']+'# changed\n')
        with patch.object(supersession,'replacement') as build:
            with self.assertRaises(ops.OperationError): supersession.supersede(self.snapshot,self.source,None)
        build.assert_not_called()
