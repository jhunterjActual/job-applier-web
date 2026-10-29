import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request
from campaign_state import Campaign, CampaignRefused
from test_campaign_state import policy
from study_ct import ScriptedCTOpener, MechanicalRefused, ENDPOINT, MODEL, LIMITS
KEY='synthetic-ct-unit-only'
SCHEMA={'type':'object','properties':{'answer':{'type':'string'}}}
def action(op): return {'operation':op,'schema':copy.deepcopy(SCHEMA),'response':{'answer':'Synthetic'}}
def request(op='fit',**changes):
 body={'model':MODEL,'max_tokens':LIMITS[op],'thinking':{'type':'between_tools'},'messages':[{'role':'user','content':'Synthetic facts/posting'}],'output_config':{'format':{'type':'json_schema','schema':SCHEMA}}};body.update(changes)
 return Request(ENDPOINT,data=json.dumps(body).encode(),headers={'x-api-key':KEY,'anthropic-version':'2023-06-01','Content-Type':'application/json'},method='POST')
class CTMechanicalTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='ct-controller-unit-');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.bound=policy();self.bound['request_limits']['CT']={'fit':1,'materials':2,'packet':3};self.campaign=Campaign.create(self.root/'state.jsonl',self.bound);self.campaign.begin_stage('CT','V01','fit')
 def opener(self,stage='fit',actions=None):return ScriptedCTOpener(self.campaign,'V01',stage,self.root/stage,KEY,actions or [action('fit')])
 def consume(self,opener,op='fit'):
  with opener.open(request(op),timeout=90) as response:
   self.assertEqual(1,self.campaign.snapshot()['pending_calls']);self.assertIn(b'Synthetic',response.read())
 def test_exact_bytes_and_reserve_before_response_consumption(self):
  opener=self.opener();req=request()
  with opener.open(req,timeout=90) as response:
   self.assertEqual(req.data,(self.root/'fit/request-001.json').read_bytes());self.assertEqual(1,self.campaign.snapshot()['admitted_requests']);response.read();self.assertFalse(opener.complete())
  self.assertTrue(opener.complete());self.assertEqual(0,self.campaign.snapshot()['exposure_microdollars'])
 def test_fit_tailor_hq_shared_monotonic_packet(self):
  self.consume(self.opener());self.campaign.finish_stage('succeeded','fixture/fit');self.campaign.begin_stage('CT','V01','materials');opener=self.opener('materials',[action('tailor'),action('headquarters')]);self.consume(opener,'tailor');self.consume(opener,'headquarters');self.assertEqual(3,self.campaign.snapshot()['admitted_requests']);self.assertTrue(opener.complete())
  with self.assertRaises(MechanicalRefused):opener.open(request('headquarters'),timeout=90)
  self.assertFalse(opener.complete())
 def test_partial_response_freezes(self):
  with self.opener().open(request(),timeout=90) as response:response.read(1)
  self.assertTrue(self.campaign.snapshot()['halted'])
  with self.assertRaises(CampaignRefused):self.campaign.finish_stage('failed','fixture/failed')
 def test_unconsumed_response_retains_pending_on_resume(self):
  self.opener().open(request(),timeout=90);reopened=Campaign.open(self.root/'state.jsonl',self.bound);self.assertEqual(1,reopened.snapshot()['pending_calls'])
  with self.assertRaises(CampaignRefused):reopened.reserve('other','0.000001','fixture/other')
 def test_actions_copied(self):
  actions=[action('fit')];opener=self.opener(actions=actions);actions[0]['response']['answer']='mutated';self.consume(opener);self.assertNotIn(b'mutated',(self.root/'fit/response-001.json').read_bytes())
 def test_contract_rejections_before_admission(self):
  opener=self.opener()
  for changes in ({'model':'other'},{'max_tokens':True},{'max_tokens':1},{'temperature':0},{'thinking':{}},{'messages':[{'role':'user','content':[{'type':'image'}]}]},{'output_config':{}}):
   with self.subTest(changes=changes),self.assertRaises(MechanicalRefused):opener.open(request(**changes),timeout=90)
  self.assertEqual(0,self.campaign.snapshot()['admitted_requests'])
 def test_route_headers_timeout_rejected(self):
  opener=self.opener()
  for url in ('https://api.anthropic.com/v1/messages/count_tokens','http://127.0.0.1/messages'):
   req=request();req.full_url=url
   with self.assertRaises(MechanicalRefused):opener.open(req,timeout=90)
  req=request();req.add_header('Authorization','synthetic-only')
  with self.assertRaises(MechanicalRefused):opener.open(req,timeout=90)
  with self.assertRaises(MechanicalRefused):opener.open(request(),timeout=True)
 def test_duplicate_header_maps_refuse_before_admission(self):
  variants=[('headers','X-API-KEY',KEY),('unredirected_hdrs','X-api-key',KEY),('unredirected_hdrs','Anthropic-version','2023-06-01'),('unredirected_hdrs','Content-type','application/json')]
  for index,(mapping,key,value) in enumerate(variants):
   opener=ScriptedCTOpener(self.campaign,'V01','fit',self.root/f'headers{index}',KEY,[action('fit')]);req=request();getattr(req,mapping)[key]=value
   with self.assertRaises(MechanicalRefused):opener.open(req,timeout=90)
   self.assertFalse(opener.complete());self.assertFalse((self.root/f'headers{index}'/'request-001.json').exists())
  self.assertEqual(0,self.campaign.snapshot()['admitted_requests'])
 def test_wrong_stage_and_existing_output_refused(self):
  with self.assertRaises(MechanicalRefused):self.opener('materials',[action('tailor'),action('headquarters')])
  self.opener()
  with self.assertRaises(MechanicalRefused):self.opener()
 def test_duplicate_nonfinite_and_schema_bool_refused(self):
  for index,raw in enumerate((request().data.replace(b'"model":',b'"model":"other","model":',1),request().data.replace(b'16384',b'NaN',1))):
   opener=ScriptedCTOpener(self.campaign,'V01','fit',self.root/f'bad{index}',KEY,[action('fit')]);req=request();req.data=raw
   with self.assertRaises(MechanicalRefused):opener.open(req,timeout=90)
   self.assertFalse(opener.complete())
  actions=[action('fit')];actions[0]['schema']['maxLength']=1
  opener=ScriptedCTOpener(self.campaign,'V01','fit',self.root/'bool-schema',KEY,actions);req=request();payload=json.loads(req.data);payload['output_config']['format']['schema']['maxLength']=True;req.data=json.dumps(payload).encode()
  with self.assertRaises(MechanicalRefused):opener.open(req,timeout=90)
  self.assertEqual(0,self.campaign.snapshot()['admitted_requests'])
 def test_pending_request_refuses_before_new_body_files(self):
  self.consume(self.opener());self.campaign.finish_stage('succeeded','fixture/fit');self.campaign.begin_stage('CT','V01','materials');opener=self.opener('materials',[action('tailor'),action('headquarters')]);response=opener.open(request('tailor'),timeout=90)
  with self.assertRaises(MechanicalRefused):opener.open(request('headquarters'),timeout=90)
  self.assertFalse((self.root/'materials/request-002.json').exists());self.assertFalse(opener.complete());self.assertEqual(1,self.campaign.snapshot()['pending_calls'])
  with response:response.read()
  self.assertFalse(opener.complete())
 def test_parent_traversal_output_refused(self):
  with self.assertRaises(MechanicalRefused):ScriptedCTOpener(self.campaign,'V01','fit',self.root/'..'/'outside',KEY,[action('fit')])
 def test_invalid_action_element_has_typed_refusal(self):
  with self.assertRaises(MechanicalRefused):self.opener(actions=[action('fit'),None])
if __name__=='__main__':unittest.main()
