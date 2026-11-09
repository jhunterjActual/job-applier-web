"""No actual urllib opener/socket is constructed or called."""
import unittest
from urllib.request import Request
from campaign_transport import CompleteResponse,Refused
from ct_campaign_opener import CtCampaignOpener,ENDPOINT

class Fake:
    def __init__(self):self.calls=[]
    def exchange(self,*args):self.calls.append(args);return CompleteResponse(200,'application/json',b'{"scripted":true}','synthetic/receipt')

class Tests(unittest.TestCase):
    def request(self):return Request(ENDPOINT,data=b'{"original":"bytes"}',method='POST',headers={'Content-Type':'application/json','anthropic-version':'2023-06-01','x-api-key':'synthetic-only'})
    def test_exact_caller_timeout_and_bytes_response_read_close_no_settlement(self):
        for limit in [1,90,1.5]:
            fake=Fake();opener=CtCampaignOpener(fake,{'fixed':'binding'});request=self.request()
            result=opener.open(request,timeout=limit);self.assertEqual(fake.calls[0][-1],limit);self.assertEqual(fake.calls[0][1],request.data)
            self.assertEqual(result.read(),b'{"scripted":true}');result.close();self.assertEqual(len(fake.calls),1)
            with self.assertRaises(Refused):opener.open(request,timeout=limit)
    def test_invalid_timeout_missing_bool_nonfinite_refuse_zero_calls(self):
        for limit in [None,True,False,0,91,float('nan'),float('inf'),'90']:
            fake=Fake()
            with self.assertRaises(Refused):CtCampaignOpener(fake,{}).open(self.request(),timeout=limit)
            self.assertEqual(fake.calls,[])
    def test_duplicate_header_dictionaries_and_endpoint_method_subclass_refuse(self):
        request=self.request();request.unredirected_hdrs['X-API-Key']='shadow'
        bad=[request,Request(ENDPOINT+'?extra=1',data=b'bytes'),Request(ENDPOINT,method='GET')]
        class Child(Request):pass
        bad.append(Child(ENDPOINT,data=b'bytes'))
        for request in bad:
            fake=Fake()
            with self.assertRaises(Refused):CtCampaignOpener(fake,{}).open(request,timeout=90)
            self.assertEqual(fake.calls,[])
    def test_unknown_encoding_control_and_mismatched_length_refuse(self):
        for name,value in [('Transfer-Encoding','chunked'),('anthropic-unknown','x'),('Content-Length','999'),('User-Agent','bad\nvalue')]:
            request=self.request();request.headers[name]=value;fake=Fake()
            with self.assertRaises(Refused):CtCampaignOpener(fake,{}).open(request,timeout=90)
            self.assertEqual(fake.calls,[])

if __name__=='__main__':unittest.main()
