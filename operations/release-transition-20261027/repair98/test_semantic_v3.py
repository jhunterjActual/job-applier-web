"""Actual validators reject rehashed coherent but unapproved intent/browser changes."""
import copy,datetime as dt,json
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch
import repair_contract as c
class SemanticTests(unittest.TestCase):
 def test_browser_actual_maps_not_equal_arbitrary_strings(self):
  live={'browser':{'public-file':'hash'}};h=c.sha(c.canonical(live['browser']));p={'browser_manifest_sha256':h}
  c.validate_browser_binding({'browser_before_sha256':h,'browser_after_sha256':h},p,live)
  with self.assertRaises(RuntimeError):c.validate_browser_binding({'browser_before_sha256':'a'*64,'browser_after_sha256':'a'*64},p,live)
 def test_rehashed_unapproved_intents_refused(self):
  plan={'repair_ops_commit':'o','source_manifest_sha256':'source','old_failure_chain_sha256':'fail','retained_runtime_manifest_sha256':'runtime','decision_at':'2026-10-27T00:00:00+00:00','expires_at':'2026-10-27T01:00:00+00:00'}
  manifest={'files':{'run_checks_repair98.py':{'sha256':'runner'},'fixture_relocation.py':{'sha256':'helper'}}};ns={'BROWSER':'browser','sandbox_properties':lambda *a:['ProtectSystem=strict']}
  def closed(name,values):return {'schema':c.SCHEMAS[name][0],'attempt_id':c.ATTEMPT_ID,**values}
  intent=closed('unit-intent.json',{'unit':c.UNIT,'argv':c.expected_unit_command(plan,manifest,ns),'sandbox_properties':['ProtectSystem=strict'],'runner_sha256':'runner','qualifier_sha256':'helper','repair_plan_sha256':'plan','repair_decision_sha256':'decision','source_manifest_sha256':'source','boot_id':'boot','coordinator_pid':10,'coordinator_start_ticks':'11','created_at':'2026-10-27T00:01:00+00:00','description_binding':'sha256-of-this-record'})
  coord=closed('coordinator.json',{'pid':10,'start_ticks':'11','boot_id':'boot','owner_pid':9,'started_at':'2026-10-27T00:00:00+00:00'})
  for field in (None,'repair_plan_sha256','repair_decision_sha256','source_manifest_sha256','runner_sha256','qualifier_sha256','coordinator_pid','coordinator_start_ticks','argv','sandbox_properties'):
   item=copy.deepcopy(intent)
   if field:item[field]=(['UNAPPROVED'] if field in ('argv','sandbox_properties') else (999 if field=='coordinator_pid' else 'wrong'))
   ir=c.canonical(item);ih=c.sha(ir)
   client=closed('test-client.json',{'pid':12,'start_ticks':'13','boot_id':'boot','unit':c.UNIT,'unit_intent_sha256':ih});cr=c.canonical(client)
   state={'LoadState':'loaded','ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0','Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'a'*32,'Description':'careertrellis-repair98:'+ih}
   value=closed('unit-terminal.json',{'unit':c.UNIT,'boot_id':'boot','client_exit':0,'client_pid':12,'client_start_ticks':'13','unit_intent_sha256':ih,'test_client_sha256':c.sha(cr),'observed_at':'2026-10-27T00:02:00+00:00','execution':{'unit':c.UNIT,'state':state,'invocation_id':'a'*32,'cgroup':{'processes':0,'populated':0}},'after':{'LoadState':'not-found'},'cgroup_after':{'processes':0,'populated':0}})
   records={'unit-intent.json':ir,'test-client.json':cr,'coordinator.json':c.canonical(coord)}
   with patch.object(c,'read',side_effect=lambda p:records[p.name]),patch.object(c,'absent_original',return_value='absent'):
    if field:
     with self.subTest(field=field),self.assertRaises(RuntimeError):c.validate_unit_terminal(value,'boot',plan,manifest,'plan','decision',ns)
    else:c.validate_unit_terminal(value,'boot',plan,manifest,'plan','decision',ns)
if __name__=='__main__':unittest.main()
