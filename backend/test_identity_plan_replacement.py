"""Private synthetic inputs only; proposal output never authorizes selection."""
from copy import deepcopy
import hashlib
import json
import unittest
from uuid import uuid4

import admission_operations as ops
import factor_release_packet as source
import identity_plan_packet as packet
import identity_plan_replacement as replacement
from test_factor_release_packet import fixture as source_fixture
from test_identity_plan_packet import fixture as plan_fixture


class IdentityPlanReplacementTests(unittest.TestCase):
    def setUp(self):
        self.plan=plan_fixture(); self.archive=packet.canonical(ops.encoded(self.plan))
        self.pointer={'version':1,'digest':hashlib.sha256(self.archive).hexdigest(),
            'operation':self.plan['bind']['provider']['operation'],'sequence':4}
        self.selection=ops.encoded(self.pointer)
        self.target=source.source_packet(source_fixture(),base='a'*40)

    def propose(self,**changes):
        return replacement.propose(changes.get('selection',self.selection),
            changes.get('archive',self.archive),changes.get('target',self.target))

    def test_exact_private_input_hashes_target_metadata_and_proposal_digest(self):
        proposal=self.propose()
        self.assertEqual(hashlib.sha256(self.selection).hexdigest(),proposal['predecessor_selection_sha256'])
        self.assertEqual(hashlib.sha256(self.archive).hexdigest(),proposal['predecessor_archive_sha256'])
        self.assertEqual(4,proposal['predecessor_sequence'])
        self.assertEqual(self.target['source_manifest_sha256'],proposal['target_source_manifest_sha256'])
        self.assertEqual(self.target['base'],proposal['target_source_base'])
        self.assertEqual(self.target['build'],proposal['target_source_build'])
        self.assertEqual(source.SCHEMA,proposal['target_schema'])
        body={key:value for key,value in proposal.items() if key!='proposal_sha256'}
        self.assertEqual(hashlib.sha256(ops.encoded(body)).hexdigest(),proposal['proposal_sha256'])

    def test_selection_requires_exact_archive_digest_and_member_operation(self):
        for field,value in (('digest','0'*64),('operation',str(uuid4())),('operation',None)):
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.propose(selection=ops.encoded({**self.pointer,field:value}))
        with self.assertRaises(ValueError): self.propose(archive=self.archive+b' ')
        other={**self.pointer,'operation':self.plan['switch']['provider']['operation']}
        self.assertEqual(4,self.propose(selection=ops.encoded(other))['predecessor_sequence'])

    def test_sequence_and_pointer_types_fail_closed(self):
        for sequence in (True,False,0,-1,1.0,'4',None,9223372036854775806):
            with self.subTest(sequence=sequence):
                with self.assertRaises(ValueError): self.propose(selection=ops.encoded({**self.pointer,'sequence':sequence}))
        for selection in ('not bytes',b'[]',b'{}',b'x'*1025):
            with self.assertRaises(ValueError): self.propose(selection=selection)
        for version in (True,2,'1'):
            with self.assertRaises(ValueError): self.propose(selection=ops.encoded({**self.pointer,'version':version}))
        with self.assertRaises(ValueError): self.propose(selection=ops.encoded({**self.pointer,'authorized':True}))

    def test_duplicate_json_fields_and_malformed_archive_rejected(self):
        with self.assertRaises(ValueError): self.propose(selection=b'{"version":1,"version":1}')
        for archive in ('not bytes',b'[]',b'x'*(packet.LIMIT+1)):
            with self.assertRaises(ValueError): self.propose(archive=archive)

    def test_version2_recovery_packet_cannot_be_initial_replacement(self):
        successor=deepcopy(self.plan); successor.update(version=2,predecessor=self.pointer['digest'])
        successor['switch']['provider']['source_application']=successor['switch']['provider']['application']
        archive=packet.canonical(ops.encoded(successor))
        selection=ops.encoded({**self.pointer,'digest':hashlib.sha256(archive).hexdigest()})
        with self.assertRaises(ValueError): self.propose(selection=selection,archive=archive)

    def test_source_manifest_hash_and_entry_format_are_checked(self):
        wrong=deepcopy(self.target); wrong['source_manifest_sha256']='0'*64
        with self.assertRaises(ValueError): self.propose(target=wrong)
        wrong=deepcopy(self.target); first=next(iter(wrong['source_manifest']))
        wrong['source_manifest'][first]='0'*64
        with self.assertRaises(ValueError): self.propose(target=wrong)
        for manifest in ({},[],{'backend/file.py':'not-a-digest'},{1:'a'*64}):
            with self.assertRaises(ValueError): self.propose(target={**self.target,'source_manifest':manifest})

    def test_source_metadata_schema_build_and_authority_checked(self):
        for key,value in (('kind','other'),('phase','release_ready'),('schema','20260915_0016'),
                          ('build','bad'),('build',78),('base','short'),('base',True),
                          ('execution_authorized',True),('execution_authorized',0)):
            with self.subTest(key=key,value=value):
                with self.assertRaises(ValueError): self.propose(target={**self.target,key:value})

    def test_output_has_no_selectable_packet_or_private_identity_material(self):
        result=self.propose(); raw=ops.encoded(result)
        with self.assertRaises(ops.OperationError): packet.validate(raw)
        for name in ('bind','switch'):
            provider=self.plan[name]['provider']
            for value in (provider['operation'],provider['owner_proof'],provider['target_binding']):
                self.assertNotIn(value.encode(),raw)
        self.assertNotIn(b'user_synthetic_owner',raw)
        self.assertNotIn('source_manifest',result)

    def test_caller_claims_cannot_clear_live_gates(self):
        result=self.propose(target={**self.target,'unstarted_verified':True,
            'selection_change_supported':True,'required_evidence':[],'pending':[]})
        for key in ('execution_authorized','selection_change_supported','unstarted_verified'):
            self.assertIs(result[key],False)
        self.assertEqual(list(replacement.REQUIRED_EVIDENCE),result['required_evidence'])
        self.assertEqual('20260928.78',result['target_source_build'])

    def test_absent_or_prepared_unchanged_pointer_requires_live_evidence(self):
        result=self.propose()
        for state in ('absent','prepared'):
            self.assertEqual('proposal_only_live_evidence_required',replacement.reconcile(result,
                observed_selection_sha256=result['predecessor_selection_sha256'],attempt_state=state))

    def test_unknown_or_interrupted_write_never_replays_even_if_pointer_unchanged(self):
        result=self.propose()
        for state in ('selection_write_started','unknown'):
            for observed in (None,result['predecessor_selection_sha256'],'0'*64):
                self.assertEqual('stop_unknown_outcome_no_replay',replacement.reconcile(result,
                    observed_selection_sha256=observed,attempt_state=state))

    def test_missing_observation_or_changed_and_aba_pointer_fail_closed(self):
        result=self.propose()
        aba_hash=hashlib.sha256(ops.encoded({**self.pointer,'sequence':6})).hexdigest()
        for state in ('absent','prepared'):
            self.assertEqual('stop_unknown_outcome_no_replay',replacement.reconcile(result,
                observed_selection_sha256=None,attempt_state=state))
            for observed in ('0'*64,aba_hash):
                self.assertEqual('stop_selection_changed_review_lineage',replacement.reconcile(result,
                    observed_selection_sha256=observed,attempt_state=state))

    def test_reconcile_rejects_tampered_proposal_and_self_rehashed_authority_claims(self):
        original=self.propose()
        changed={**original,'predecessor_sequence':5}
        with self.assertRaises(ValueError): replacement.reconcile(changed,observed_selection_sha256=None,attempt_state='absent')
        for key in ('execution_authorized','selection_change_supported','unstarted_verified'):
            changed={**original,key:True}
            body={name:value for name,value in changed.items() if name!='proposal_sha256'}
            changed['proposal_sha256']=hashlib.sha256(ops.encoded(body)).hexdigest()
            with self.assertRaises(ValueError): replacement.reconcile(changed,observed_selection_sha256=None,attempt_state='absent')

    def test_reconcile_rejects_unrecognized_states_and_malformed_observations(self):
        result=self.propose()
        for state in ('complete','retry_safe','',None):
            with self.assertRaises(ValueError): replacement.reconcile(result,
                observed_selection_sha256=result['predecessor_selection_sha256'],attempt_state=state)
        for observed in (True,1,'short','G'*64):
            with self.assertRaises(ValueError): replacement.reconcile(result,
                observed_selection_sha256=observed,attempt_state='prepared')

    def test_rehashed_malformed_proposal_does_not_bypass_structural_validation(self):
        original=self.propose()
        malformed=[]
        missing=original.copy(); del missing['target_source_build']; malformed.append(missing)
        for key,value in (('required_evidence',[]),('target_schema','20260915_0016'),
                          ('predecessor_sequence',True),('execution_authorized',0),
                          ('selection_change_supported',0),('unstarted_verified',0),
                          ('unsupported_authority',True)):
            malformed.append({**original,key:value})
        for changed in malformed:
            body={key:value for key,value in changed.items() if key!='proposal_sha256'}
            changed['proposal_sha256']=hashlib.sha256(ops.encoded(body)).hexdigest()
            with self.subTest(keys=tuple(changed)):
                with self.assertRaises(ValueError) as caught:
                    replacement.reconcile(changed,observed_selection_sha256=None,attempt_state='prepared')
                self.assertEqual('Initial plan replacement proposal rejected.',str(caught.exception))


if __name__=='__main__': unittest.main()
