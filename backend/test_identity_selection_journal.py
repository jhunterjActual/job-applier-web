"""Pure two-host journal rehearsal; all pointers and packets are synthetic."""
from copy import deepcopy
import hashlib
import itertools
import unittest
from uuid import uuid4

import admission_operations as ops
import factor_release_packet as source
import identity_plan_packet as packet
import identity_plan_replacement as replacement
import identity_selection_journal as journal
from test_factor_release_packet import fixture as source_fixture
from test_identity_plan_packet import fixture as plan_fixture


def sha(raw): return hashlib.sha256(raw).hexdigest()


class IdentitySelectionJournalTests(unittest.TestCase):
    def setUp(self):
        plan=plan_fixture(); archive=packet.canonical(ops.encoded(plan))
        self.old={'version':1,'digest':sha(archive),'operation':plan['bind']['provider']['operation'],'sequence':4}
        self.web=ops.encoded(self.old)
        self.database=ops.encoded({**self.old,'sequence':7})
        target=source.source_packet(source_fixture(),base='a'*40)
        self.proposal=replacement.propose(self.web,archive,target)
        self.transaction=str(uuid4()); self.operation=str(uuid4()); self.target='f'*64
        self.prior={'web':self.web,'database':self.database}
        self.record=self.prepare()

    def prepare(self,**changes):
        return journal.prepare(changes.get('proposal',self.proposal),
            transaction=changes.get('transaction',self.transaction),
            target_digest=changes.get('target_digest',self.target),
            target_operation=changes.get('target_operation',self.operation),
            prior_selections=changes.get('prior_selections',self.prior))

    def claimed(self):
        web=journal.claim(self.record,'web',expected_sha256=self.record['journal_sha256'])
        return journal.claim(web,'database',expected_sha256=web['journal_sha256'])

    def observations(self,record,web='prior',database='prior'):
        def one(host,state):
            if state=='missing': return None
            return {'journal_sha256':'0'*64 if state=='stale' else record['journal_sha256'],
                'selection_sha256':record['hosts'][host]['prior_sha256' if state in ('prior','stale') else 'target_sha256']
                    if state!='conflict' else '1'*64}
        return {host:one(host,state) for host,state in (('web',web),('database',database))}

    def assess(self,record,observations):
        history=[self.record]
        if isinstance(record,dict) and type(record.get('revision')) is int:
            for host in journal.HOSTS[:record['revision']]:
                previous=history[-1]
                history.append(journal.claim(previous,host,expected_sha256=previous['journal_sha256']))
        return journal.assess(record,observations,history=history)

    def assert_fixed_error(self,call):
        with self.assertRaises(ValueError) as caught: call()
        self.assertEqual(journal.ERROR,str(caught.exception))

    def test_prepare_binds_two_exact_pointers_and_independent_sequences(self):
        result=self.record
        self.assertEqual(self.transaction,result['transaction'])
        self.assertEqual(self.proposal['proposal_sha256'],result['proposal_sha256'])
        self.assertEqual(0,result['revision']); self.assertIsNone(result['previous_sha256'])
        for host,raw in self.prior.items():
            old=ops.strict_json(raw)
            expected={**old,'digest':self.target,'operation':self.operation,'sequence':old['sequence']+1}
            item=result['hosts'][host]
            self.assertEqual(sha(raw),item['prior_sha256'])
            self.assertEqual(sha(ops.encoded(expected)),item['target_sha256'])
            self.assertEqual(old['sequence'],item['prior_sequence'])
            self.assertEqual(old['sequence']+1,item['target_sequence'])
            self.assertEqual('prepared',item['state'])
        self.assertEqual(sha(ops.encoded({k:v for k,v in result.items() if k!='journal_sha256'})),result['journal_sha256'])
        self.assertIs(result['execution_authorized'],False); self.assertIs(result['consumers_enabled'],False)

    def test_prepare_requires_both_hosts_and_exact_web_proposal_snapshot(self):
        for selections in ({}, {'web':self.web}, {**self.prior,'other':self.web},
                           {**self.prior,'web':ops.encoded({**self.old,'sequence':5})},
                           {**self.prior,'database':ops.encoded({**self.old,'digest':'0'*64})},
                           {**self.prior,'database':ops.encoded({**self.old,'operation':str(uuid4())})}):
            with self.subTest(selections=tuple(selections)):
                self.assert_fixed_error(lambda:self.prepare(prior_selections=selections))

    def test_prepare_rejects_bad_ids_reuse_hashes_and_pointer_types(self):
        for key,value in (('transaction','bad'),('transaction',True),('target_digest','G'*64),
                          ('target_digest',self.old['digest']),('target_operation',self.old['operation']),
                          ('target_operation','bad'),('target_operation',None)):
            with self.subTest(key=key): self.assert_fixed_error(lambda:self.prepare(**{key:value}))
        for raw in (None,'not-bytes',b'{}',b'[]',b'x'*1025,
                    ops.encoded({**self.old,'sequence':True}),
                    ops.encoded({**self.old,'sequence':0}),
                    ops.encoded({**self.old,'sequence':9223372036854775806}),
                    ops.encoded({**self.old,'operation':'bad'})):
            self.assert_fixed_error(lambda:self.prepare(prior_selections={**self.prior,'database':raw}))

    def test_claim_is_immutable_exact_cas_with_explicit_lineage(self):
        before=deepcopy(self.record)
        claimed=journal.claim(self.record,'web',expected_sha256=self.record['journal_sha256'])
        self.assertEqual(before,self.record)
        self.assertEqual(1,claimed['revision']); self.assertEqual(self.record['journal_sha256'],claimed['previous_sha256'])
        self.assertEqual('publication_claimed',claimed['hosts']['web']['state'])
        self.assertEqual('prepared',claimed['hosts']['database']['state'])
        final=journal.claim(claimed,'database',expected_sha256=claimed['journal_sha256'])
        self.assertEqual(2,final['revision']); self.assertEqual(claimed['journal_sha256'],final['previous_sha256'])
        for host in journal.HOSTS:
            self.assertEqual('publication_claimed',final['hosts'][host]['state'])
            self.assertEqual(before['hosts'][host]['target_sha256'],final['hosts'][host]['target_sha256'])

    def test_repeated_stale_unknownhost_or_rebased_claim_rejected(self):
        claimed=journal.claim(self.record,'web',expected_sha256=self.record['journal_sha256'])
        for host,digest in (('web',claimed['journal_sha256']),('database',self.record['journal_sha256']),
                            ('other',claimed['journal_sha256']),('database',None)):
            self.assert_fixed_error(lambda:journal.claim(claimed,host,expected_sha256=digest))
        changed=deepcopy(claimed); changed['hosts']['database']['prior_sequence']+=1
        self.assert_fixed_error(lambda:journal.claim(changed,'database',expected_sha256=changed['journal_sha256']))

    def test_each_prior_target_combination_is_blocked_after_both_claims(self):
        claimed=self.claimed()
        expected={('prior','prior'):'not_selected_blocked_no_replay',
            ('target','prior'):'partial_selection_blocked',('prior','target'):'partial_selection_blocked',
            ('target','target'):'converged_requires_independent_acceptance'}
        for states,outcome in expected.items():
            with self.subTest(states=states):
                self.assertEqual(outcome,self.assess(claimed,self.observations(claimed,*states)))
                self.assertIs(claimed['execution_authorized'],False)
                self.assertIs(claimed['consumers_enabled'],False)

    def test_prepared_and_unclaimed_target_never_imply_publication(self):
        self.assertEqual('prepared_blocked',self.assess(self.record,self.observations(self.record)))
        for states in (('target','prior'),('prior','target'),('target','target')):
            self.assertEqual('selection_conflict_blocked',self.assess(self.record,self.observations(self.record,*states)))
        claimed=journal.claim(self.record,'web',expected_sha256=self.record['journal_sha256'])
        self.assertEqual('partial_selection_blocked',self.assess(claimed,self.observations(claimed,'target','prior')))
        self.assertEqual('selection_conflict_blocked',self.assess(claimed,self.observations(claimed,'target','target')))

    def test_missing_conflicting_or_stale_journal_observation_for_each_host(self):
        claimed=self.claimed()
        expected={'missing':'unknown_blocked_no_replay','conflict':'selection_conflict_blocked',
                  'stale':'journal_disagreement_blocked'}
        for index in (0,1):
            for state,outcome in expected.items():
                states=['prior','prior']; states[index]=state
                with self.subTest(host=journal.HOSTS[index],state=state):
                    self.assertEqual(outcome,self.assess(claimed,self.observations(claimed,*states)))

    def test_all_observation_combinations_cannot_enable_consumers(self):
        claimed=self.claimed(); original=deepcopy(claimed)
        for states in itertools.product(('prior','target','missing','stale','conflict'),repeat=2):
            outcome=self.assess(claimed,self.observations(claimed,*states))
            self.assertIn(outcome,('not_selected_blocked_no_replay','partial_selection_blocked',
                'converged_requires_independent_acceptance','unknown_blocked_no_replay',
                'selection_conflict_blocked','journal_disagreement_blocked'))
            self.assertIs(claimed['consumers_enabled'],False)
        self.assertEqual(original,claimed)

    def test_old_journal_observation_after_new_claim_is_disagreement(self):
        observations=self.observations(self.record)
        claimed=journal.claim(self.record,'web',expected_sha256=self.record['journal_sha256'])
        self.assertEqual('journal_disagreement_blocked',self.assess(claimed,observations))

    def test_malformed_observations_are_fixed_errors(self):
        for observations in (None,{}, {'web':None}, {**self.observations(self.record),'other':None},
            {**self.observations(self.record),'web':{}},
            {**self.observations(self.record),'web':{'journal_sha256':True,'selection_sha256':'0'*64}},
            {**self.observations(self.record),'web':{'journal_sha256':'0'*64,'selection_sha256':'bad'}}):
            self.assert_fixed_error(lambda:self.assess(self.record,observations))

    def test_malformed_or_rehashed_records_cannot_bypass_protocol(self):
        records=[None,{}, {**self.record,'unsupported':True}, {**self.record,'journal_sha256':'0'*64}]
        for field,value in (('transaction','bad'),('target_operation',True),('revision',True),
                            ('previous_sha256','0'*64),('execution_authorized',True),('consumers_enabled',True)):
            changed=deepcopy(self.record); changed[field]=value
            records.append(journal.seal({k:v for k,v in changed.items() if k!='journal_sha256'}))
        for field,value in (('prior_sequence',True),('target_sequence',10),('state','published'),
                            ('target_sha256',self.record['hosts']['web']['prior_sha256']),('target_sha256','2'*64)):
            changed=deepcopy(self.record); changed['hosts']['web'][field]=value
            records.append(journal.seal({k:v for k,v in changed.items() if k!='journal_sha256'}))
        for record in records:
            with self.subTest(record_type=type(record).__name__):
                self.assert_fixed_error(lambda:journal.claim(record,'web',expected_sha256=self.record['journal_sha256']))
                self.assert_fixed_error(lambda:self.assess(record,self.observations(self.record)))

    def test_missing_ancestry_fork_and_rehashed_changed_bindings_rejected(self):
        first=journal.claim(self.record,'web',expected_sha256=self.record['journal_sha256'])
        final=journal.claim(first,'database',expected_sha256=first['journal_sha256'])
        fork=journal.claim(self.record,'database',expected_sha256=self.record['journal_sha256'])
        for history in ([],[final],[self.record,final],[self.record,fork,final]):
            self.assert_fixed_error(lambda:journal.assess(final,self.observations(final),history=history))
        changed=deepcopy(final); changed['transaction']=str(uuid4())
        changed=journal.seal({key:value for key,value in changed.items() if key!='journal_sha256'})
        self.assert_fixed_error(lambda:journal.assess(changed,self.observations(changed),
            history=[self.record,first,changed]))
        self.assertEqual('converged_requires_independent_acceptance',journal.assess(final,
            self.observations(final,'target','target'),history=(self.record,first,final)))


if __name__=='__main__': unittest.main()
