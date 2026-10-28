"""Synthetic pair/transport control flow only; native IO boundaries are explicit mocks."""
import unittest,copy,io,json,types
from datetime import datetime,timezone
from unittest.mock import patch
import preplan_program as p
import session_server as server
import native_io as n
NOW=datetime(2000,1,1,tzinfo=timezone.utc)
class Clock(datetime):
 @classmethod
 def now(cls,tz=None):return NOW
class Tests(unittest.TestCase):
 def fixture(self,mutation=None,ackmutation=None):
  web=object.__new__(p.NativeSession);web.role='web';web.boot='11111111-1111-1111-1111-111111111111';web.actor={'pid':123,'parent_pid':2,'birth':'456','state':'S'};web.program_sha='a'*64;web.profile=dict(p.BASE_PROFILE);web.started=web.finished=NOW.isoformat()
  dbpeer={'schema':'careertrellis.preplan-private-peer/v1','role':'db','boot_id':'22222222-2222-2222-2222-222222222222','actor':{'pid':124,'parent_pid':2,'birth':'789','state':'S'},'program_sha256':web.program_sha,'source_commit':p.OPS,'packet_sha256':p.PACKET,'readiness_manifest_sha256':p.READINESS,'scan_started_at':NOW.isoformat(),'scan_finished_at':NOW.isoformat()}
  contract={'schema':'careertrellis.app-plan-source-contract/v1','mode':'actual','source_commit':p.OPS,'packet_sha256':p.PACKET,'readiness_manifest_sha256':p.READINESS,'collector_source_sha256':web.program_sha}
  request={'nonce':'b'*64,'issued_at':NOW.isoformat(),'boots':{'web':web.boot,'db':dbpeer['boot_id']},'context_sha256':n.sha(n.canonical(contract))};web.request=request
  def host(role):
   e={'role':role,'nonce':request['nonce'],'boot_id':request['boots'][role],'source_commit':p.OPS,'context_sha256':request['context_sha256'],'synthetic_io_seam':True}
   policy={'schema':'careertrellis.paired-maintenance-policy/v1','application_release':'synthetic-baseline','private_marker':'SYNTHETIC_PRIVATE'}
   return {'schema':'careertrellis.app-plan-host-facts/v1','role':role,'boot_id':request['boots'][role],'nonce':request['nonce'],'source_commit':p.OPS,'observed_at':NOW.isoformat(),'context_sha256':request['context_sha256'],'private_policy':copy.deepcopy(policy),'public_policy':copy.deepcopy(policy),'evidence_sha256':n.sha(n.canonical(e)),'_private_evidence':e}
  webpeer={'schema':'careertrellis.preplan-private-peer/v1','role':'web','boot_id':web.boot,'actor':web.actor,'program_sha256':web.program_sha,'source_commit':p.OPS,'packet_sha256':p.PACKET,'readiness_manifest_sha256':p.READINESS,'scan_started_at':web.started,'scan_finished_at':web.finished}
  web.challenge_result={'schema':'careertrellis.preplan-private-challenge/v1','peer':webpeer,'request':request,'host':host('web'),'scan_proof':{'historical_bindings':p.HISTORY,'source_diffs':p.DIFFS},'raw_health':{'evaluation_generation_sha256':'1'*64},'accepted_pair_hex':'00'}
  dbcap={'schema':'careertrellis.preplan-private-challenge/v1','peer':dbpeer,'request':copy.deepcopy(request),'host':host('db'),'scan_proof':None,'raw_health':{'evaluation_generation_sha256':'2'*64,'repository':{}},'accepted_pair_hex':'00'}
  web.public={};web.accepted_pair_raw=b'SYNTHETIC';web.idle=types.SimpleNamespace(bridge=types.SimpleNamespace(verify_repository_binding=lambda *a:None),health_evidence={'schema':'SYNTHETIC_HEALTH_IO_SEAM','raw_web_generation_sha256':'1'*64,'raw_db_generation_sha256':'2'*64,'database_generation_sha256':'3'*64})
  if mutation:mutation(dbcap)
  ack={'schema':'careertrellis.preplan-private-stable/v1','role':'db','nonce':request['nonce'],'program_sha256':web.program_sha,'boot_id':dbpeer['boot_id'],'actor':dbpeer['actor'],'challenge_sha256':n.sha(n.canonical(dbcap)),'checked_at':NOW.isoformat(),'database_generation_sha256':'3'*64}
  if ackmutation:ackmutation(ack)
  stream=io.BytesIO(n.canonical({'seq':3,'ok':True,'value':ack})+b'\n');wire=io.BytesIO();proxy=server.DBProxy(wire,stream,web.program_sha);proxy.seq=3;return web,dbpeer,dbcap,proxy,contract,wire
 def invoke(self,mutation=None,ackmutation=None):
  web,peer,db,proxy,contract,wire=self.fixture(mutation,ackmutation)
  with patch.object(p.native_pair,'validate',return_value={'synthetic_native_pair_seam':True}),patch.object(p,'datetime',Clock),patch.object(p.NativeSession,'identity_current',return_value=None),patch.object(p.NativeSession,'stable',return_value=None):
   facts=p.PreparedFacts(web,peer,proxy);result=facts.bind(contract,db)
  return result,facts,wire
 def test_pair_actual_methods_synthetic_positive(self):
  facts,proof,wire=self.invoke();self.assertEqual(set(facts['hosts']),{'web','db'});self.assertIn(b'db_rpc',wire.getvalue());self.assertNotIn(b'SYNTHETIC_PRIVATE',wire.getvalue())
 def test_nonce_boot_source_and_context_drift_refused(self):
  for field,value in [('nonce','c'*64),('boot_id','33333333-3333-3333-3333-333333333333'),('source_commit','c'*40),('context_sha256','d'*64)]:
   with self.subTest(field=field):self.assertRaises(n.Refused,self.invoke,lambda c:c['host'].__setitem__(field,value))
 def test_raw_evidence_hash_mismatch_refuses(self):self.assertRaises(n.Refused,self.invoke,lambda c:c['host']['_private_evidence'].__setitem__('role','web'))
 def test_dback_role_boot_source_digest_and_future_time_refuse(self):
  for field,value in [('role','web'),('nonce','c'*64),('program_sha256','c'*64),('boot_id','wrong'),('challenge_sha256','f'*64),('database_generation_sha256','9'*64),('checked_at','2000-01-01T00:00:01+00:00')]:
   with self.subTest(field=field):self.assertRaises(n.Refused,self.invoke,None,lambda a:a.__setitem__(field,value))
 def test_private_facts_mutation_refuses_before_callback(self):
  result,facts,_=self.invoke();bad=copy.deepcopy(result);bad['hosts']['web']['private_policy']['private_marker']='ALTERED'
  with patch.object(p.NativeSession,'stable') as stable:self.assertRaises(n.Refused,facts.authenticate,bad,facts.contract);stable.assert_not_called()
 def test_wrong_source_factory_callback_rejected(self):
  web,peer,_,_,_,_=self.fixture();self.assertRaises(n.Refused,p.PreparedFacts,web,peer,lambda:True)
