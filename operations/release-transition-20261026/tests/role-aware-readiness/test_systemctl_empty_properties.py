"""Realistic synthetic systemctl empty suppression; no native query/cause claim."""
import ast,importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
import portable_bootstrap as pb
import collector as c
P=Path(__file__).parent
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
baseline=load('baseline',P/'fixtures/systemctl-preall-collector.reference.py')
class Show:
 def __init__(self,omit=None,extra=False):self.calls=[];self.omit=omit;self.extra=extra
 def __call__(self,args,maximum):
  self.calls.append((args,maximum));assert args[:2]==['/usr/bin/systemctl','show']
  selected=[a for a in args if a.startswith('--property=')];assert len(selected)==1
  fields=selected[0].split('=',1)[1].split(',');assert len(set(fields))==len(fields)
  names=[a for a in args[2:] if not a.startswith('--')];assert len(names)==1
  name=names[0];assert name in ('careertrellis-web.service','careertrellis-paired-recovery.timer','careertrellis-paired-maintenance.timer')
  values={'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','MainPID':'0','ControlPID':'0','Job':'','Result':'success','FragmentPath':'/etc/systemd/system/'+name,'DropInPaths':'','NeedDaemonReload':'no','InvocationID':'','NRestarts':'0','UnitFileState':'static','TimersCalendar':'','TimersMonotonic':'','Persistent':'yes','RandomizedDelayUSec':'0','AccuracyUSec':'1s'}
  if name.endswith('.timer'):values.update(ActiveState='active',SubState='waiting',UnitFileState='enabled')
  if name=='careertrellis-paired-maintenance.timer':values['TimersCalendar']='OnCalendar=Sun *-*-* 03:00:00 America/New_York'
  if name=='careertrellis-paired-recovery.timer':values['TimersMonotonic']='synthetic audit'
  # systemctl without --all suppresses empty properties. --all does not
  # override --property selection; no invented/default missing field is emitted.
  rows=[k+'='+values[k]+'\n' for k in fields if k!=self.omit and ('--all' in args or values[k]!='')]
  if self.extra:rows.append('Unexpected=not_allowed\n')
  return ''.join(rows).encode()
class Tests(unittest.TestCase):
 def test_baseline_realistic_empty_omissions_refuse(self):
  for kind,name in [('unit','careertrellis-web.service'),('unit','careertrellis-paired-recovery.timer'),('timer_cadence','careertrellis-paired-recovery.timer'),('timer_cadence','careertrellis-paired-maintenance.timer')]:
   show=Show()
   with self.subTest(kind=kind,name=name),patch.object(baseline,'run',show),self.assertRaises(baseline.Refused):getattr(baseline.Reader(),kind)(name)
   self.assertNotIn('--all',show.calls[0][0])
 def test_proposed_exact_families_empty_strings_preserved_and_stable(self):
  for name,fields in [('careertrellis-web.service',c.SERVICE_UNIT_FIELDS+('InvocationID','NRestarts','UnitFileState')),('careertrellis-paired-recovery.timer',c.TIMER_UNIT_FIELDS)]:
   show=Show()
   with self.subTest(name=name),patch.object(c,'run',show):
    reader=c.Reader();value=reader.unit(name);reader.stable()
    self.assertEqual(set(value),set(fields));self.assertEqual(value['Job'],'');self.assertEqual(value['DropInPaths'],'')
    if name.endswith('.service'):self.assertEqual(value['InvocationID'],'')
    else:self.assertNotIn('MainPID',value)
   self.assertEqual(len(show.calls),2)
   for args,maximum in show.calls:
    self.assertEqual(args.count('--all'),1);self.assertEqual(maximum,16384);self.assertEqual(set(next(a for a in args if a.startswith('--property=')).split('=',1)[1].split(',')),set(fields))
 def test_proposed_cadence_closed_filtered_empty_values_stable(self):
  fields={'TimersCalendar','TimersMonotonic','Persistent','RandomizedDelayUSec','AccuracyUSec'}
  for name,empty in [('careertrellis-paired-recovery.timer','TimersCalendar'),('careertrellis-paired-maintenance.timer','TimersMonotonic')]:
   show=Show()
   with self.subTest(name=name),patch.object(c,'run',show):
    reader=c.Reader();value=reader.timer_cadence(name);reader.stable();self.assertEqual(set(value),fields);self.assertEqual(value[empty],'')
   for args,maximum in show.calls:
    self.assertEqual(args.count('--all'),1);self.assertEqual(maximum,16384);self.assertEqual(set(next(a for a in args if a.startswith('--property=')).split('=',1)[1].split(',')),fields)
 def test_all_still_refuses_actual_missing_or_unknown_keys(self):
  for kind,name,omit in [('unit','careertrellis-web.service','Job'),('unit','careertrellis-web.service','InvocationID'),('unit','careertrellis-paired-recovery.timer','LoadState'),('timer_cadence','careertrellis-paired-recovery.timer','TimersCalendar')]:
   with self.subTest(kind=kind,omit=omit),patch.object(c,'run',Show(omit=omit)),self.assertRaises(c.Refused):getattr(c.Reader(),kind)(name)
  for kind in ('unit','timer_cadence'):
   with patch.object(c,'run',Show(extra=True)),self.assertRaises(c.Refused):getattr(c.Reader(),kind)('careertrellis-paired-recovery.timer')
 def test_exact_two_argv_additions_all_other_ast_and_raw_unchanged(self):
  old=(P/'fixtures/systemctl-preall-collector.reference.py').read_bytes();new=(pb.ROOT/'one_shot_readiness/collector.py').read_bytes();needle=b"'/usr/bin/systemctl','show','--all',name,"
  self.assertEqual(new.count(needle),2);self.assertEqual(new.replace(needle,b"'/usr/bin/systemctl','show',name,"),old)
  def functions(raw):
   lines=raw.splitlines(True);return {n.name:b''.join(lines[n.lineno-1:n.end_lineno]) for n in ast.walk(ast.parse(raw)) if isinstance(n,ast.FunctionDef)}
  a=functions(old);b=functions(new)
  self.assertEqual({n for n in a if a[n]!=b[n]},{'unit','timer_cadence'})
  self.assertEqual(a['unit_properties'],b['unit_properties']);self.assertEqual(a['read'],b['read']);self.assertEqual(a['read_artifact'],b['read_artifact']);self.assertEqual(a['effective_artifact_map'],b['effective_artifact_map'])
if __name__=='__main__':unittest.main()
