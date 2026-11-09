"""One-call scripted urllib facade; no real opener/credential lookup."""
import io
import threading
import re
from urllib.request import Request
from email.message import Message
from campaign_transport import Refused,timeout as validate_timeout

ENDPOINT='https://api.anthropic.com/v1/messages'

class Response(io.BytesIO):
    def __init__(self,complete):
        super().__init__(complete.body);self.status=complete.status;self.code=complete.status
        self.headers=Message();self.headers['Content-Type']=complete.content_type
        self.receipt_reference=complete.receipt_reference
    def getcode(self):return self.status
    def info(self):return self.headers

class CtCampaignOpener:
    def __init__(self,broker,binding):
        self.broker=broker;self.binding=dict(binding);self.lock=threading.Lock();self.used=False
    def open(self,request,timeout=None):
        # Caller timeout is mandatory; no native90 -> core180 substitution.
        validate_timeout(timeout,90)
        if type(request) is not Request or request.full_url!=ENDPOINT or request.get_method()!='POST' or not isinstance(request.data,bytes):raise Refused('native_urllib_request')
        seen=set();public=[];private={};total=0
        for name,value in [*request.headers.items(),*request.unredirected_hdrs.items()]:
            if not isinstance(name,str) or not isinstance(value,str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+",name) or not value.isascii() or len(value)>1024 or any(ord(c)<32 or ord(c)==127 for c in value):raise Refused('header_type')
            key=name.lower();total+=len(name)+len(value)
            if key in seen or total>8192 or len(seen)>=32:raise Refused('shadowed_or_oversize_headers')
            seen.add(key)
            if key in ('x-api-key','authorization'):private[name]=value
            elif key in ('content-type','anthropic-version','accept','anthropic-beta'):public.append([name,value])
            elif key.startswith('anthropic-'):raise Refused('unknown_anthropic_header')
            elif key in ('transfer-encoding','content-encoding'):raise Refused('encoded_request')
            elif key=='content-length' and value!=str(len(request.data)):raise Refused('content_length')
        with self.lock:
            if self.used:raise Refused('no_facade_replay')
            self.used=True
        complete=self.broker.exchange(self.binding,request.data,public,private,timeout)
        return Response(complete)
