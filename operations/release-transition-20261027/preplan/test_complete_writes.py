"""Exact byte completion, no RPC retries; all streams synthetic memory only."""
import unittest,io,json
import private_channel as c
import native_io as n
class Partial:
 def __init__(self,size,fault=None):self.raw=bytearray();self.size=size;self.calls=0;self.flushes=0;self.fault=fault
 def write(self,raw):
  self.calls+=1
  if self.fault=='zero':return 0
  if self.fault=='bool':return True
  if self.fault=='over':return len(raw)+1
  if self.fault=='mid' and self.calls==2:raise OSError('SYNTHETIC_PRIVATE_MIDFRAME')
  count=min(self.size,len(raw));self.raw.extend(raw[:count]);return count
 def flush(self):self.flushes+=1
class Tests(unittest.TestCase):
 def test_actual_send_completes_multiple_positive_shortwrites(self):
  stream=Partial(17);value={'payload':'\u00e9'*1000};c.send(stream,value);expected=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')+b'\n';self.assertEqual(bytes(stream.raw),expected);self.assertGreater(stream.calls,1);self.assertEqual(stream.flushes,1)
 def test_actual_send_zero_bool_overcount_or_midframe_refuses_no_flush(self):
  for fault in ('zero','bool','over','mid'):
   stream=Partial(2,fault)
   with self.assertRaises((n.Refused,OSError)):c.send(stream,{'PRIVATE_SENTINEL':'synthetic'})
   self.assertEqual(stream.flushes,0);self.assertEqual(stream.calls,2 if fault=='mid' else 1)
