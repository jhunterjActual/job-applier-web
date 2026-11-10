"""Actual staging methods reject original challenge expiry/boot drift before any intent."""
import unittest,types,contextlib,copy
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from contextlib import ExitStack
import public_delivery as d
import native_io as n
import test_public_delivery as fixtures
class Tests(unittest.TestCase):
 def setup_context(self):
  profile={'source_commit':'c'*40,'packet_sha256':'d'*64,'readiness_manifest_sha256':'e'*64};start=datetime(2000,1,1,tzinfo=timezone.utc);binding=fixtures.Tests().binding(profile,start);state={'boot':binding['boots']['web'],'pointers':{},'binding_sha256':n.sha(n.canonical(binding))};clock={'now':start,'boot':state['boot']}
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return clock['now']
  return profile,binding,state,clock,Clock
 def test_actual_preflight_rechecks_after_slow_installed_reads_and_boot_change(self):
  for fault in ('elapsed','boot','binding'):
   profile,binding,state,clock,Clock=self.setup_context();reached=[]
   def slow(reader):
    reached.append('installed')
    if fault=='elapsed':clock['now']+=timedelta(seconds=31)
    elif fault=='boot':clock['boot']='33333333-3333-3333-3333-333333333333'
    else:binding['nonce']='f'*64
    return {}
   c=types.SimpleNamespace(Reader=lambda:types.SimpleNamespace(stable=lambda:reached.append('stable')),installed493=slow,native_custody=lambda *a:None,authority=lambda *a:None)
   @contextlib.contextmanager
   def modules(_):yield c
   with patch.object(d,'datetime',Clock),patch.object(d,'bootstrap',return_value=state['boot']),patch.object(d,'boot',side_effect=lambda:clock['boot']),patch.object(d,'clear_kernel_locks'),patch.object(d,'readonly_modules',modules),patch.object(d,'pointer_state',side_effect=lambda:reached.append('pointers') or {}),patch.object(d,'create') as create:self.assertRaises(n.Refused,d.preflight,'web',{},profile,binding);create.assert_not_called()
   self.assertEqual(reached,['installed','stable','pointers'])
 def test_actual_packet_capacity_delay_or_boot_drift_zero_intents(self):
  for fault in ('elapsed','boot'):
   profile,binding,state,clock,Clock=self.setup_context();reached=[]
   def capacity(_):
    reached.append('capacity')
    if fault=='elapsed':clock['now']+=timedelta(seconds=31)
    else:clock['boot']='33333333-3333-3333-3333-333333333333'
    return types.SimpleNamespace(f_bavail=6*1024**3,f_frsize=1,f_favail=10000)
   with patch.object(d,'datetime',Clock),patch.object(d,'bundle',return_value=({'runtime':profile},b'PUBLIC_PACKET',{})),patch.object(d,'preflight',return_value=state),patch.object(d.os.path,'lexists',return_value=False),patch.object(d.os,'statvfs',side_effect=capacity,create=True),patch.object(d,'boot',side_effect=lambda:clock['boot']),patch.object(d,'process_identity',return_value={'pid':123,'birth':'456'}),patch.object(d,'deferred_child_signals',contextlib.nullcontext),patch.object(d,'create') as create:self.assertRaises(n.Refused,d.stage_packet,'web',b'x','a'*64,{},binding);create.assert_not_called()
   self.assertEqual(reached,['capacity'])
 def test_actual_plan_rechecks_after_stored_packet_and_stability_reads(self):
  for fault in ('stored-delay','stable-delay','stable-boot'):
   case=fixtures.Tests();plan,raw,profile,start=case.plan();binding=case.binding(profile,start,plan['boots']);state={'boot':plan['boots']['web'],'pointers':{},'binding_sha256':n.sha(n.canonical(binding))};clock={'now':start,'boot':state['boot']};reached=[]
   class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return clock['now']
   def stable():
    reached.append('stable')
    if fault=='stable-delay':clock['now']+=timedelta(seconds=31)
    if fault=='stable-boot':clock['boot']='33333333-3333-3333-3333-333333333333'
   def stored(*_):
    reached.append('stored')
    if fault=='stored-delay':clock['now']+=timedelta(seconds=31)
    return types.SimpleNamespace(stable=stable)
   with patch.object(d,'datetime',Clock),patch.object(d,'bundle',return_value=({'runtime':profile},b'PUBLIC_PACKET',{})),patch.object(d,'preflight',return_value=state),patch.object(d,'stored_packet',side_effect=stored),patch.object(d.os.path,'lexists',return_value=False),patch.object(d.os,'statvfs',return_value=types.SimpleNamespace(f_bavail=6*1024**3,f_frsize=1,f_favail=10000),create=True),patch.object(d,'boot',side_effect=lambda:clock['boot']),patch.object(d,'process_identity',return_value={'pid':123,'birth':'456'}),patch.object(d,'deferred_child_signals',contextlib.nullcontext),patch.object(d,'create') as create:self.assertRaises(n.Refused,d.stage_plan,'web',b'x','a'*64,{},binding,raw,n.sha(raw));create.assert_not_called()
   self.assertEqual(reached,['stored','stable'])
