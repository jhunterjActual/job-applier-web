"""Pure bounded compression for an independently reviewed exact source wire."""
import base64,hashlib,re,zlib
def compressed_wire(source,approved_sha):
 if not(type(source)is bytes and 0<len(source)<=4194304 and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and hashlib.sha256(source).hexdigest()==approved_sha):raise ValueError('EXACT_SOURCE_WIRE_REQUIRED')
 compressed=zlib.compress(source,9)
 text='''import base64,hashlib,zlib
def decode_wire(packed,expected_length,expected_sha):
 if type(packed)is not bytes or len(packed)>1048576 or type(expected_length)is not int or not 0<expected_length<=4194304:raise ValueError()
 decoder=zlib.decompressobj()
 raw=decoder.decompress(packed,expected_length+1)
 if not(decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data and len(raw)==expected_length and hashlib.sha256(raw).hexdigest()==expected_sha):raise ValueError()
 return raw
try:
 raw=decode_wire(base64.b64decode(PACKED,validate=True),EXPECTED_LENGTH,EXPECTED_SHA)
except BaseException:
 print('{"result":"compressed_source_wire_refused","no_replay":true}')
 raise SystemExit(1)
exec(compile(raw,'<reviewed-source-wire>','exec'),{'__name__':'__main__'})
'''
 values={'PACKED':repr(base64.b64encode(compressed).decode('ascii')),'EXPECTED_LENGTH':str(len(source)),'EXPECTED_SHA':repr(approved_sha)}
 result=re.sub(r'\b(?:PACKED|EXPECTED_LENGTH|EXPECTED_SHA)\b',lambda m:values[m.group()],text).encode('utf8')
 if len(result)>1048576:raise ValueError('COMPRESSED_WIRE_EXCEEDS_CARRIER_LIMIT')
 return result
