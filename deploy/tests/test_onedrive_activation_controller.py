"""Synthetic consistency checks; no controller, host, marker or provider calls."""
import copy
import datetime as dt
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from deploy.onedrive_retention import activation_controller as c
from deploy.tests.test_onedrive_activation_transition import fixture,NOW


def chain():
    context,evidence,installation,review=fixture()
    candidate=c.t.proposal(context,evidence,installation,review,NOW);intent=candidate['intent']
    selector={'schema':c.a.SCHEMA,'transaction':intent['transaction'],'enabled':True,
        'activation_intent_sha256':c.t.digest(intent),'activated_at':(NOW+dt.timedelta(minutes=1)).isoformat(),
        'valid_until':intent['valid_until']}
    owner={'pid':123,'start_ticks':100,'process_group':123,'boot_id':'11111111-1111-1111-1111-111111111111'}
    receipts={};acks={};states={}
    for role in ('web','db'):
        receipt={'transaction':intent['transaction'],'role':role,'phase':'commit','intent_sha256':c.t.digest(intent),
            'selector_sha256':c.t.digest(selector),'result':'committed','committed_at':(NOW+dt.timedelta(minutes=2)).isoformat()}
        ack={'schema':c.ACK,'transaction':intent['transaction'],'role':role,'intent_sha256':c.t.digest(intent),
            'selector_sha256':c.t.digest(selector),'request_sha256':'1'*64,'receipt_sha256':c.t.digest(receipt),
            'receipt':receipt,'received_at':(NOW+dt.timedelta(minutes=3)).isoformat(),
            'provenance':c.provenance(role,owner,context.bindings)}
        state={'schema':c.RECONCILIATION,'transaction':intent['transaction'],'role':role,'intent_sha256':c.t.digest(intent),
            'selector_sha256':c.t.digest(selector),'observed_at':(NOW+dt.timedelta(minutes=4)).isoformat(),
            'committed_receipt_sha256':c.t.digest(receipt),'observation_sha256':'2'*64,
            'provenance':c.provenance(role,owner,context.bindings)}
        receipts[role]=receipt;acks[role]=ack;states[role]=state
    baselines={'web':c.t.digest(context.document),'db':'3'*64}
    terminal={'schema':c.TERMINAL,'transaction':intent['transaction'],'intent_sha256':c.t.digest(intent),
        'selector_sha256':c.t.digest(selector),'baseline_bindings':context.bindings,'baseline_sha256':baselines,
        'installation_receipts':installation,'committed_receipts':{role:c.t.digest(doc) for role,doc in receipts.items()},
        'commit_acknowledgements':{role:c.t.digest(doc) for role,doc in acks.items()},
        'reconciliations':{role:c.t.digest(doc) for role,doc in states.items()},'owner':owner,
        'source_manifest_sha256':context.bindings['standalone_manifest_sha256'],'result':'both_commits_verified',
        'unknown_outcome':False,'completed_at':(NOW+dt.timedelta(minutes=5)).isoformat(),
        'prepare_deadline':candidate['prepare_deadline']}
    arguments={'intent':intent,'selector':selector,'bindings':context.bindings,'baseline_sha256':baselines,
        'installation_receipts':installation,'committed_receipts':receipts,'commit_acknowledgements':acks,
        'reconciliations':states,'now':NOW+dt.timedelta(hours=1)}
    return terminal,arguments


class Tests(unittest.TestCase):
    def test_consistent_terminal_remains_unauthenticated(self):
        terminal,arguments=chain();result=c.validate_terminal(terminal,**arguments)
        self.assertEqual(result,{'authenticated':False,'terminal_consistent':True})
        self.assertNotEqual(terminal['baseline_sha256']['web'],terminal['baseline_sha256']['db'])

    def test_unknown_lost_ack_missing_role_or_digest_drift_refuses(self):
        for mutation in ('unknown','missing','selector','ack','source','baseline','late'):
            terminal,arguments=chain()
            if mutation=='unknown':terminal['unknown_outcome']=True
            elif mutation=='missing':arguments['commit_acknowledgements'].pop('db')
            elif mutation=='selector':terminal['selector_sha256']='9'*64
            elif mutation=='ack':terminal['commit_acknowledgements']['db']='9'*64
            elif mutation=='source':terminal['source_manifest_sha256']='9'*64
            elif mutation=='baseline':terminal['baseline_sha256']=dict(terminal['baseline_sha256'],web='9'*64)
            else:terminal['completed_at']=terminal['prepare_deadline']
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):c.validate_terminal(terminal,**arguments)

    def test_ack_owner_kind_receipt_and_temporal_drift_refuses_even_rehashed(self):
        for mutation in ('owner','kind','receipt','time','reconciliation'):
            terminal,arguments=chain();ack=arguments['commit_acknowledgements']['db']
            if mutation=='owner':ack['provenance']['owner']['pid']=999
            elif mutation=='kind':ack['provenance']['kind']='caller-assertion'
            elif mutation=='receipt':ack['receipt_sha256']='9'*64
            elif mutation=='time':ack['received_at']=(NOW+dt.timedelta(minutes=6)).isoformat()
            else:arguments['reconciliations']['db']['committed_receipt_sha256']='9'*64
            terminal['commit_acknowledgements']['db']=c.t.digest(ack)
            terminal['reconciliations']['db']=c.t.digest(arguments['reconciliations']['db'])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):c.validate_terminal(terminal,**arguments)

    def test_selector_equality_is_required_for_both_hosts(self):
        terminal,arguments=chain();arguments['committed_receipts']['db']['selector_sha256']='9'*64
        with self.assertRaises(ValueError):c.validate_terminal(terminal,**arguments)

    def test_checkout_and_passed_participant_cannot_authenticate_native(self):
        with self.assertRaises(ValueError):c.native('d'*32)
        with self.assertRaises(ValueError):c.NativeController(Mock(),object())
        self.assertTrue(c.capabilities()['native_dispatch_available'])
        self.assertFalse(c.capabilities()['activation_authority_established'])

    def test_actor_state_source_policy_owner_and_original_request_must_match(self):
        terminal,arguments=chain();bindings=arguments['bindings']
        source={'paired_entry_sha256':'4'*64,'actor_sha256':'5'*64}
        actor={'schema':'careertrellis.retention-activation-actor-state/v1','transaction':terminal['transaction'],
            'intent_sha256':terminal['intent_sha256'],'actor_owner':{'pid':222,'start_ticks':333,
                'boot_id':'11111111-1111-1111-1111-111111111111'},'actor_invocation':'6'*32,
            'prepare_request_sha256':'7'*64,'baseline_bindings':bindings,
            'baseline_sha256':terminal['baseline_sha256']['db'],'source':source}
        args=(terminal['transaction'],terminal['intent_sha256'],bindings,terminal['baseline_sha256']['db'],'7'*64,source)
        c.validate_actor_state(actor,*args)
        for key,value in (('prepare_request_sha256','9'*64),('actor_invocation','0'*32),
                ('baseline_sha256','9'*64),('actor_owner',{'pid':0,'start_ticks':333,'boot_id':'bad'}),
                ('source',dict(source,actor_sha256='9'*64))):
            broken=copy.deepcopy(actor);broken[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):c.validate_actor_state(broken,*args)

    def test_authority_producer_uses_actual_cached_prepare_acks_and_one_selector(self):
        # Source-only producer fixture: native factory and all I/O are mocked.
        terminal,arguments=chain();context,*unused=fixture();intent=arguments['intent']
        local={'prepare_deadline':terminal['prepare_deadline'],'installation_receipts':arguments['installation_receipts'],
            'peer_baseline_sha256':terminal['baseline_sha256']['db']}
        web_owner={'pid':123,'start_ticks':100,'boot_id':terminal['owner']['boot_id']}
        db_owner=dict(web_owner,pid=456,start_ticks=200)
        actor=object.__new__(c.NativeController);actor.context=context;actor.transaction=intent['transaction']
        actor.root=Path('/synthetic');actor._check=Mock(return_value=context);actor._intent=Mock(return_value=(intent,local))
        documents={};actor._actual_prepares={}
        for role,birth in (('web',web_owner),('db',db_owner)):
            common=c.prepare_request(role,intent,local['prepare_deadline'],context.bindings)
            receipt={'transaction':intent['transaction'],'role':role,'phase':'prepare','intent_sha256':c.t.digest(intent),
                'request_sha256':c.t.digest(common),'result':'prepared'}
            ack={'schema':c.PREPARE_ACK,'transaction':intent['transaction'],'role':role,'intent_sha256':c.t.digest(intent),
                'request_sha256':'7'*64,'receipt_sha256':c.t.digest(receipt),'receipt':receipt,'actor_owner':birth,
                'received_at':(NOW+dt.timedelta(minutes=1)).isoformat(),
                'provenance':c.provenance(role,terminal['owner'],context.bindings)}
            documents['prepared-'+role+'.json']=receipt;documents['prepare-ack-'+role+'.json']=ack
            actor._actual_prepares[role]=c.t.digest(ack)
        observation={'actor_owner':db_owner,'actor_invocation':'6'*32}
        actor._observe_actor=Mock(return_value=observation)
        documents['prepare-db.dispatch.json']={'request':{'synthetic':True}}
        documents['db-actor-state-prepare.json']=observation;written={}
        def reader(path):
            document=documents[path.name];return c.bundle.canonical(document),document
        def exclusive(path,value,**kwargs):
            written[path.name]=copy.deepcopy(value);return c.t.digest(value)
        with patch.object(c.t,'_private',side_effect=reader),patch.object(c.t,'_exclusive_marker',side_effect=exclusive),\
                patch.object(c,'now',return_value=NOW+dt.timedelta(minutes=2)),\
                patch.object(c,'_owner',return_value=terminal['owner']),patch.object(c.t,'_birth',return_value=web_owner):
            result=actor.produce_commit_authorities()
            self.assertFalse(result['db_delivery_performed'])
            self.assertEqual(written['commit-authority.json']['selector'],written['commit-authority-db.json']['selector'])
            self.assertNotEqual(written['commit-authority.json']['dispatcher_owner'],written['commit-authority-db.json']['dispatcher_owner'])
            self.assertEqual(written['commit-authority.json']['selector']['valid_until'],intent['valid_until'])
            written.clear();actor._actual_prepares.clear()
            with self.assertRaises(ValueError):actor.produce_commit_authorities()
            self.assertEqual(written,{})

    def test_unknown_local_commit_is_recorded_and_never_replayed(self):
        # Isolated control-flow fixture; no native factory is authenticated.
        terminal,arguments=chain();context,*unused=fixture()
        intent=arguments['intent'];selector=arguments['selector'];deadline=terminal['prepare_deadline']
        authority={'schema':'careertrellis.retention-activation-commit-authority/v1',
            'transaction':intent['transaction'],'intent_sha256':c.t.digest(intent),'prepare_deadline':deadline,
            'dispatcher_owner':{},'prepared':{},'selector':selector,'selector_sha256':c.t.digest(selector)}
        actor=object.__new__(c.NativeController);actor.context=context;actor.transaction=intent['transaction']
        actor.root=Path('/synthetic');actor._check=Mock(return_value=context)
        actor._intent=Mock(return_value=(intent,{'prepare_deadline':deadline}))
        actor.participant=Mock();actor.participant.commit.side_effect=OSError('lost acknowledgement')
        actor.collect_web_readiness=Mock() # Separate tests cover native readiness production.
        actor._actual_acks={'db':c.t.digest(arguments['commit_acknowledgements']['db'])};actor._actual_states={}
        actor._actual_authorities={'web_authority_sha256':c.t.digest(authority)}
        records={};owner=terminal['owner']
        def reader(path):
            document=authority if path.name=='commit-authority.json' else arguments['commit_acknowledgements']['db']
            return c.bundle.canonical(document),document
        def exclusive(path,value,**kwargs):
            if path.name in records:raise FileExistsError()
            records[path.name]=copy.deepcopy(value);return c.t.digest(value)
        with patch.object(c.t,'_private',side_effect=reader),patch.object(c.t,'_exclusive_marker',side_effect=exclusive),\
                patch.object(c,'now',return_value=NOW+dt.timedelta(minutes=3)),patch.object(c,'_owner',return_value=owner):
            with self.assertRaises(ValueError):actor.commit_once('web')
            with self.assertRaises(FileExistsError):actor.commit_once('web')
        actor.participant.commit.assert_called_once()
        self.assertEqual(set(records),{'commit-web.dispatch.json','commit-web.unknown.json'})
        self.assertNotIn('web',actor._actual_acks)

    def test_unknown_local_prepare_is_recorded_and_never_replayed(self):
        terminal,arguments=chain();context,*unused=fixture();intent=arguments['intent']
        actor=object.__new__(c.NativeController);actor.context=context;actor.transaction=intent['transaction']
        actor.root=Path('/synthetic');actor._check=Mock(return_value=context)
        actor._intent=Mock(return_value=(intent,{'prepare_deadline':terminal['prepare_deadline']}))
        prior={'synthetic':'actual-client-response-mocked'}
        actor._actual_prepares={'db':c.t.digest(prior)}
        actor._require_pending=Mock()
        actor.collect_web_readiness=Mock();actor.participant=Mock()
        actor.participant.prepare.side_effect=OSError('lost acknowledgement');records={}
        def exclusive(path,value,**kwargs):
            if path.name in records:raise FileExistsError()
            records[path.name]=copy.deepcopy(value);return c.t.digest(value)
        with patch.object(c.t,'_private',return_value=(c.bundle.canonical(prior),prior)),\
                patch.object(c.t,'_exclusive_marker',side_effect=exclusive),\
                patch.object(c,'now',return_value=NOW+dt.timedelta(minutes=2)),\
                patch.object(c,'_owner',return_value=terminal['owner']):
            with self.assertRaises(ValueError):actor.prepare_once('web')
            with self.assertRaises(FileExistsError):actor.prepare_once('web')
        actor.participant.prepare.assert_called_once()
        self.assertEqual(set(records),{'prepare-web.dispatch.json','prepare-web.unknown.json'})
        self.assertNotIn('web',actor._actual_prepares)

    def test_no_pending_fence_means_no_db_prepare_dispatch(self):
        context,*unused=fixture();actor=object.__new__(c.NativeController)
        actor._check=Mock(return_value=context);actor._actual_pending=None
        actor._client=Mock()
        with patch.object(c.t,'_exclusive_marker') as writer:
            with self.assertRaises(ValueError):actor.prepare_once('db')
            actor._client.assert_not_called();writer.assert_not_called()

    def test_changed_pending_fence_cannot_be_consumed(self):
        actor=object.__new__(c.NativeController);actor._actual_pending='1'*64
        with patch.object(c.a,'PENDING',Path('/synthetic/pending.json'),create=True),\
                patch.object(c.t,'_private',return_value=(b'{}',{'changed':True})):
            with self.assertRaises(ValueError):actor._require_pending()


if __name__=='__main__':unittest.main()
