"""Scripted-only Campaign transport. No socket, credential or provider adapter.

Create before owner.begin_stage; share one live Broker across threads. Reopening
supports inspection only. Durable pending/unknown is never automatically retried.
"""
from dataclasses import dataclass
from decimal import Decimal,Context,localcontext,ROUND_CEILING
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import types
import uuid

CAMPAIGN_SHA='b0394af9fa065f601840b53c03ccba39dc874e758c824503764917342fab16e6'
BUDGET_SHA='a142625a224ff192c4c7093e144dc4993b459839e98777e700b85a1ae0b47a1c'
ENVELOPE_SHA='992f6a0b45200753f6071460d024e32a24dac14ed194f8a3b79f93f8b9f7d844'
MODEL='claude-sonnet-5-5'
OPS={'ct_fit':('CT','fit',16384),'ct_tailor':('CT','materials',32768),
     'ct_headquarters':('CT','materials',4096),'up_fit':('UP','fit',32768),'up_materials':('UP','materials',32768)}
ROLES=('count_mapper','counter','sender','usage_parser','response_guard')
_BROKER_OWNERS=set()
_OWNER_LOCK=threading.Lock()

class Refused(ValueError):pass
class Unknown(Refused):pass
class NativeRefused(Refused):pass
class NoSend(Refused):
    """Only scripted mapper/refusal paths may prove no dispatch occurred."""

def digest(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def strict_json(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise Refused('duplicate_json')
            result[k]=v
        return result
    def bad(_):raise Refused('nonfinite_json')
    try:return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs,parse_constant=bad)
    except (UnicodeError,ValueError,RecursionError):raise Refused('invalid_json') from None
def fields(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys.split()):raise Refused('exact_fields')
def integer(x,low,high):return type(x) is int and low<=x<=high
def label(x):
    if not isinstance(x,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',x):raise Refused('identifier')
    return x
def hash_value(x):
    if not isinstance(x,str) or not re.fullmatch('[0-9a-f]{64}',x):raise Refused('hash')
    return x
def timeout(value,ceiling):
    if type(value) not in (int,float) or not 1<=value<=ceiling or not math.isfinite(value):raise Refused('timeout')
    return value
def checked_path(path):
    path=Path(path).absolute()
    if '..' in path.parts:raise Refused('path_traversal')
    for item in [path,*path.parents]:
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):raise Refused('linked_path')
    if path.exists() and path.is_file() and path.stat().st_nlink!=1:raise Refused('linked_file')
    return path
def file_hash(path):
    path=checked_path(path)
    if not path.is_file() or path.stat().st_size>1024**2:raise Refused('source_bound')
    return digest(path.read_bytes())
def fsync_dir(path):
    # Windows directory durability is a separate integration gate. Pure Windows
    # tests mock THIS boundary, never claim it is Linux filesystem evidence.
    if os.name!='posix':raise Refused('native_directory_fsync_unverified')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

@dataclass(frozen=True)
class ScriptedAdapter:
    role:str
    function:object
    source_sha256:str

@dataclass(frozen=True)
class CompleteResponse:
    status:int
    content_type:str
    body:bytes
    receipt_reference:str

class Broker:
    @classmethod
    def create(cls,root,campaign,frozen_policy,count_mapper,counter,sender,usage_parser,response_guard,*,reservation_calculator):
        self=cls.__new__(cls);self.lock=threading.RLock();self.campaign=campaign
        self._frozen_raw=canonical(frozen_policy);self.policy=strict_json(self._frozen_raw);self.adapters={a.role:a for a in (count_mapper,counter,sender,usage_parser,response_guard)}
        self.calculator=reservation_calculator;self.slots={};self.calls={};self.counts={};self.used=set();self.halted=False;self.bytes=0
        self._validate_policy();self._pins()
        state=campaign.snapshot()
        if state['active'] or state['pending_calls'] or state['halted'] or state['admitted_requests'] or state['completed_stages']:raise Refused('startup_active_pending_or_halted')
        self.root=checked_path(root)
        if not self.root.parent.is_dir():raise Refused('existing_private_parent_required')
        if os.name=='posix':
            parent=self.root.parent.stat()
            if parent.st_uid!=os.getuid() or parent.st_mode&0o022:raise Refused('private_parent_ownership')
        ownership=str(checked_path(campaign.path)).casefold() if os.name=='nt' else str(checked_path(campaign.path))
        with _OWNER_LOCK:
            if ownership in _BROKER_OWNERS:raise Refused('one_live_broker_per_campaign')
            _BROKER_OWNERS.add(ownership) # no reset/recovery API, including failed construction.
        self.root.mkdir(mode=0o700);fsync_dir(self.root.parent)
        self._write(None,'manifest.json',canonical(self.policy));return self
    @staticmethod
    def open_for_inspection(root,campaign,frozen_policy):
        root=checked_path(root);raw=(root/'manifest.json').read_bytes()
        if len(raw)>1024**2 or raw!=canonical(frozen_policy):raise Refused('inspection_manifest')
        return {'dispatch_supported':False,'campaign':campaign.snapshot(),'manifest_sha256':digest(raw),'halt_marker':(root/'halt.json').exists()}
    def _validate_policy(self):
        p=self.policy
        fields(p,'schema run_id campaign_policy_sha256 source_sha256 native_content_sha256 model pricing operations body_caps response_cap_bytes count_policy count_mapping_sha256 usage_parser_sha256 response_guard_sha256 evidence_limit_bytes generation_timeout_seconds count_timeout_seconds mode')
        if p['schema']!='broker-scripted-policy-v1' or p['mode']!='scripted' or p['model']!=MODEL:raise Refused('scripted_only')
        label(p['run_id']);hash_value(p['native_content_sha256'])
        if self.campaign.policy['schema']!='study-campaign-policy-v2' or p['run_id']!=self.campaign.policy['run_id'] or digest(canonical(self.campaign.policy))!=p['campaign_policy_sha256']:raise Refused('campaign_policy_binding')
        self.capacity=self.campaign.policy['max_inflight_requests']
        if not integer(self.capacity,1,4):raise Refused('capacity')
        if p['operations']!={k:v[2] for k,v in OPS.items()} or p['body_caps']!={'CT':16*1024**2,'UP':4*1024**2} or type(p['response_cap_bytes']) is not int or p['response_cap_bytes']!=8*1024**2:raise Refused('fixed_limits')
        if not integer(p['evidence_limit_bytes'],1,1024**3) or type(p['generation_timeout_seconds']) is not int or p['generation_timeout_seconds']!=180 or type(p['count_timeout_seconds']) is not int or p['count_timeout_seconds']!=30:raise Refused('resource_bounds')
        if set(self.adapters)!=set(ROLES) or any(not isinstance(a,ScriptedAdapter) or type(a.function) is not types.FunctionType for a in self.adapters.values()):raise Refused('explicit_scripted_injections_required')
        sources=p['source_sha256']
        if not isinstance(sources,dict) or set(sources)!={'broker','campaign','budget_guard','native_cost_envelope',*ROLES}:raise Refused('source_bindings')
        for v in sources.values():hash_value(v)
        if sources['campaign']!=CAMPAIGN_SHA or sources['budget_guard']!=BUDGET_SHA or sources['native_cost_envelope']!=ENVELOPE_SHA:raise Refused('reviewed_dependency')
        for role,key in [('count_mapper','count_mapping_sha256'),('usage_parser','usage_parser_sha256'),('response_guard','response_guard_sha256')]:
            if p[key]!=sources[role]:raise Refused('adapter_binding')
        count=p['count_policy'];fields(count,'run_limit per_stage_limits per_packet_limits')
        expected_stage={};expected_packet={};total=0
        for stage in self.campaign.policy['order']:
            product=stage['product'];case=stage['case'];name=stage['stage'];lim=self.campaign.policy['request_limits'][product]
            expected_stage[product+'/'+case+'/'+name]=lim[name];expected_packet[product+'/'+case]=lim['packet'];total+=lim[name]
        if not integer(count['run_limit'],0,min(total,256)) or set(count['per_stage_limits'])!=set(expected_stage) or set(count['per_packet_limits'])!=set(expected_packet):raise Refused('count_caps')
        for key,value in count['per_stage_limits'].items():
            if not integer(value,0,expected_stage[key]):raise Refused('count_stage_cap')
        for key,value in count['per_packet_limits'].items():
            if not integer(value,0,expected_packet[key]):raise Refused('count_packet_cap')
        fields(p['pricing'],'model input_usd_per_million output_usd_per_million source verified_utc')
        if p['pricing']['model']!=MODEL or p['pricing']['source']!='https://platform.claude.com/docs/en/about-claude/pricing' or not isinstance(p['pricing']['verified_utc'],str) or not p['pricing']['verified_utc']:raise Refused('declared_pricing')
        for k in ['input_usd_per_million','output_usd_per_million']:
            v=p['pricing'][k]
            if not isinstance(v,str) or not re.fullmatch(r'(0|[1-9][0-9]{0,8})(\.[0-9]{1,6})?',v) or Decimal(v)<=0:raise Refused('price_decimal')
    def _pins(self):
        if canonical(self.policy)!=self._frozen_raw:raise Refused('frozen_policy_drift')
        if hasattr(self,'root') and (self.root/'manifest.json').exists():self._read_verified(None,'manifest.json',digest(self._frozen_raw),len(self._frozen_raw))
        expected=self.policy['source_sha256']
        files={'broker':Path(__file__),'campaign':Path(inspect.getsourcefile(type(self.campaign))),
               'native_cost_envelope':Path(inspect.getsourcefile(self.calculator))}
        files['budget_guard']=Path(inspect.getsourcefile(inspect.getmodule(type(self.campaign)).money))
        for role,a in self.adapters.items():
            if a.source_sha256!=expected[role]:raise Refused('scripted_adapter_hash')
            files[role]=Path(inspect.getsourcefile(a.function))
        if any(file_hash(path)!=expected[name] for name,path in files.items()):raise Refused('source_drift')
    def _write(self,call,name,raw):
        # All metadata/payload writes share the same short lock; no injected
        # external work executes while this lock is held.
        with self.lock:
            if not isinstance(raw,bytes) or self.bytes+len(raw)>self.policy['evidence_limit_bytes']:raise Unknown('evidence_cap')
            # The whole intended write is charged BEFORE opening the exclusive
            # target. A short write, flush/fsync failure or uncertain open keeps
            # this reservation permanently; no other call may reuse its space.
            self.bytes+=len(raw)
            directory=self.root if call is None else self.root/call
            path=checked_path(directory/name)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            fsync_dir(directory)
            expected=digest(raw);self._read_verified(call,name,expected,len(raw));return expected
    def _read_verified(self,call,name,expected,cap):
        path=checked_path((self.root if call is None else self.root/call)/name)
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>cap:raise Unknown('saved_evidence_stat')
            raw=f.read(cap+1)
        if len(raw)>cap or digest(raw)!=expected:raise Unknown('saved_evidence_hash')
        return raw
    def _json(self,call,name,value):return self._write(call,name,canonical(value))
    def _halt(self,reason):
        with self.lock:
            self.halted=True
            if not (self.root/'halt.json').exists():
                try:self._json(None,'halt.json',{'halted':True,'classification':reason})
                except BaseException:pass # memory stays halted; existing root cannot dispatch on reopen.
    def _stage(self,binding):
        fields(binding,'run_id call_id product case stage operation');label(binding['call_id'])
        if binding['run_id']!=self.policy['run_id'] or binding['operation'] not in OPS:raise Refused('binding')
        product,stage,_=OPS[binding['operation']]
        state=self.campaign.snapshot()
        if not state['active'] or state['halted'] or self.halted or state['completed_stages']>=len(self.campaign.policy['order']):raise Refused('inactive_or_halted')
        current=self.campaign.policy['order'][state['completed_stages']]
        if current!={k:binding[k] for k in ('product','case','stage')} or (product,stage)!=(binding['product'],binding['stage']):raise Refused('exact_active_stage')
    def _remaining(self,end,cap):
        value=min(cap,end-time.monotonic())
        if value<=0:raise Unknown('exchange_deadline')
        return value
    def _adapter(self,role,*args):
        self._pins();return self.adapters[role].function(*args)
    def _terminal(self,call,state,classification):
        self._json(call,'terminal.json',{'classification':classification,'reserved':state['reserved'],'settled':state['settled'],'native_completion_proved':False})
        with self.lock:self.slots.pop(call)
    def _usage(self,result,mapping,request_sha,response_sha):
        fields(result,'fixture_id request_sha256 response_sha256 model complete input_tokens output_tokens priced_categories evidence_sha256')
        if result['fixture_id']!=mapping['fixture_id'] or result['request_sha256']!=request_sha or result['response_sha256']!=response_sha or result['model']!=MODEL or result['complete'] is not True or result['priced_categories']!=['input_tokens','output_tokens']:raise Unknown('scripted_usage_binding')
        hash_value(result['evidence_sha256'])
        if not integer(result['input_tokens'],0,10**9) or not integer(result['output_tokens'],0,10**9):raise Unknown('usage_integer')
        with localcontext(Context(prec=64)):
            p=self.policy['pricing'];amount=(Decimal(result['input_tokens'])*Decimal(p['input_usd_per_million'])+Decimal(result['output_tokens'])*Decimal(p['output_usd_per_million']))/Decimal(10**6)
            return format(amount.quantize(Decimal('.000001'),rounding=ROUND_CEILING),'.6f')
    def exchange(self,binding,raw_body,application_headers,private_headers,timeout_seconds):
        binding=strict_json(canonical(binding));ceiling=90 if binding.get('product')=='CT' else 180;timeout(timeout_seconds,ceiling)
        if not isinstance(raw_body,bytes) or len(raw_body)>self.policy['body_caps'].get(binding.get('product'),0):raise Refused('body_cap')
        headers=validate_headers(application_headers,private_headers);private_headers=dict(private_headers);native=strict_json(raw_body)
        if not isinstance(native,dict) or native.get('model')!=MODEL or type(native.get('max_tokens')) is not int or binding.get('operation') not in OPS or native['max_tokens']!=OPS[binding['operation']][2]:raise Refused('native_model_output')
        end=time.monotonic()+timeout_seconds;call=binding['call_id'];request_sha=digest(raw_body)
        with self.lock:
            self._pins();self._stage(binding)
            if call in self.used or len(self.slots)>=self.capacity:raise Refused('exchange_slot_or_duplicate')
            self.used.add(call);owner=uuid.uuid4().hex;self.slots[call]=owner
            state={'binding':binding,'owner':owner,'reserved':False,'settled':False,'send_started':False,'settle_started':False,'count_started':False,'count_complete':False,'terminal_refusal_proven':False};self.calls[call]=state
        try:
            with self.lock:(self.root/call).mkdir(mode=0o700);fsync_dir(self.root)
            self._write(call,'request.bin',raw_body)
            self._json(call,'request.json',{'binding':binding,'request_sha256':request_sha,'body_bytes':len(raw_body),'application_headers':headers,'timeout_seconds':timeout_seconds})
            self._read_verified(call,'request.bin',request_sha,len(raw_body))
            mapping=self._adapter('count_mapper',binding,raw_body,request_sha)
            fields(mapping,'fixture_id original_sha256 count_body mapping_sha256');label(mapping['fixture_id']);hash_value(mapping['mapping_sha256'])
            if mapping['original_sha256']!=request_sha or not isinstance(mapping['count_body'],bytes) or len(mapping['count_body'])>self.policy['body_caps'][binding['product']]:raise NoSend('scripted_mapping')
            with self.lock:
                self._stage(binding);cp=self.policy['count_policy'];stage='/'.join(binding[k] for k in ('product','case','stage'));packet='/'.join(binding[k] for k in ('product','case'))
                if len(self.counts)>=cp['run_limit'] or sum(x['stage']==stage for x in self.counts.values())>=cp['per_stage_limits'][stage] or sum(x['packet']==packet for x in self.counts.values())>=cp['per_packet_limits'][packet]:raise NoSend('count_allowance')
                self._write(call,'count-request.bin',mapping['count_body']);count_sha=digest(mapping['count_body'])
                self._json(call,'count-intent.json',{'owner':owner,'stage':stage,'packet':packet,'count_sha256':count_sha,'original_sha256':request_sha,'mapping_sha256':mapping['mapping_sha256']})
                self.counts[call]={'owner':owner,'stage':stage,'packet':packet,'complete':False}
            self._read_verified(call,'count-request.bin',count_sha,len(mapping['count_body']))
            state['count_started']=True
            counted=self._adapter('counter',mapping['count_body'],headers,dict(private_headers),self._remaining(end,30))
            fields(counted,'fixture_id complete input_tokens request_sha256 response_body status content_type')
            if counted['fixture_id']!=mapping['fixture_id'] or counted['complete'] is not True or not integer(counted['input_tokens'],0,100000) or counted['request_sha256']!=count_sha or type(counted['status']) is not int or counted['status']!=200 or counted['content_type']!='application/json' or not isinstance(counted['response_body'],bytes) or len(counted['response_body'])>1024**2:raise Unknown('count_completion')
            self._write(call,'count-response.bin',counted['response_body'])
            self._json(call,'count-terminal.json',{k:v for k,v in counted.items() if k!='response_body'})
            state['count_complete']=True
            with self.lock:
                self.counts[call]['complete']=True
                # A count admitted before halt may persist above, but this gate
                # cannot admit a generation after another call halts the run.
                self._stage(binding)
                if self.slots.get(call)!=owner or self.counts[call]['owner']!=owner or not self.counts[call]['complete']:raise Unknown('count_slot_ownership')
                self._pins();self._remaining(end,180)
                p=self.policy['pricing'];reserve=format(self.calculator(counted['input_tokens'],native['max_tokens'],p['input_usd_per_million'],p['output_usd_per_million']),'.6f')
                self._json(call,'reserve-evidence.json',{'reservation_usd':reserve,'count_sha256':count_sha,'request_sha256':request_sha,'pricing':p})
                self.campaign.reserve(call,reserve,'broker/'+call+'/reserve-evidence');state['reserved']=True
                self._json(call,'generation-intent.json',{'owner':owner,'request_sha256':request_sha,'logical_dispatch_admitted':True})
            if self.halted:raise Unknown('halt_before_sender')
            self._read_verified(call,'request.bin',request_sha,len(raw_body))
            self._remaining(end,180);state['send_started']=True
            response=self._adapter('sender',raw_body,headers,dict(private_headers),self._remaining(end,180))
            fields(response,'status content_type body')
            if type(response['status']) is not int or response['status'] not in (200,400,409,413,502) or response['content_type'] not in ('application/json','text/event-stream') or not isinstance(response['body'],bytes) or len(response['body'])>self.policy['response_cap_bytes']:raise Unknown('response_completion')
            response_sha=self._write(call,'response.bin',response['body'])
            self._json(call,'response.json',{'status':response['status'],'content_type':response['content_type'],'sha256':response_sha})
            usage=self._adapter('usage_parser',binding,raw_body,response['body'],{'status':response['status'],'content_type':response['content_type']})
            actual=self._usage(usage,mapping,request_sha,response_sha);self._json(call,'usage.json',usage)
            with self.lock:
                self._json(call,'settlement-intent.json',{'actual_usd':actual});state['settle_started']=True
                self.campaign.settle(call,actual,'broker/'+call+'/usage');state['settled']=True
                self._json(call,'settlement-known.json',{'actual_usd':actual})
                if self.campaign.snapshot()['halted']:self._halt('known_over_reservation')
            verdict=self._adapter('response_guard',binding,request_sha,response_sha,response['body'])
            fields(verdict,'approved request_sha256 response_sha256 fixture_id')
            self._json(call,'guard.json',verdict)
            if verdict['approved'] is not True or verdict['request_sha256']!=request_sha or verdict['response_sha256']!=response_sha or verdict['fixture_id']!=mapping['fixture_id'] or response['status']!=200:
                self._halt('known_charge_native_refusal');self._terminal(call,state,'known_charge_native_refused')
                state['terminal_refusal_proven']=True
                raise NativeRefused('known_charge_native_refused')
            self._pins();self._remaining(end,10)
            self._json(call,'delivery-started.json',{'response_sha256':response_sha,'native_completion_proved':False})
            self._terminal(call,state,'released_to_facade_native_completion_unproved')
            return CompleteResponse(response['status'],response['content_type'],response['body'],'broker/'+call+'/terminal')
        except BaseException as exc:
            # Public exception classes are NOT trusted provenance. Only this
            # call's completed internal halt+terminal transition can establish
            # a NativeRefused result; injected callbacks cannot bypass cleanup.
            if isinstance(exc,NativeRefused) and state['terminal_refusal_proven']:raise
            if state['settled']:
                self._halt('known_charge_post_settlement_failure')
                try:self._terminal(call,state,'known_charge_native_failed')
                except BaseException:pass
            elif isinstance(exc,(NoSend,Refused)) and not isinstance(exc,Unknown) and not state['reserved'] and (not state['count_started'] or state['count_complete']):
                try:self._terminal(call,state,'known_no_generation_send')
                except BaseException:self._halt('terminal_evidence_unknown')
            else:
                self._halt('exchange_unknown')
                if state['reserved'] and not state['settle_started']:
                    try:self.campaign.mark_unknown(call,'broker/'+call+'/unknown')
                    except BaseException:pass
            raise Unknown('scripted_exchange_refused_or_unknown') from None
    def report_delivery(self,call_id,outcome):
        with self.lock:
            if call_id not in self.calls or call_id in self.slots or not self.calls[call_id]['settled'] or not (self.root/call_id/'delivery-started.json').is_file() or outcome not in ('guest_staged_only','native_failed','delivery_unknown'):raise Refused('delivery_receipt')
            try:self._json(call_id,'delivery-result.json',{'outcome':outcome,'native_completion_proved':False})
            except BaseException:self._halt('delivery_evidence_unknown');raise Unknown('delivery_evidence_unknown') from None
            if outcome!='guest_staged_only':self._halt('known_charge_delivery_failure')

def validate_headers(public,private):
    if not isinstance(public,(list,tuple)) or len(public)>4 or not isinstance(private,dict) or len(private)>2:raise Refused('headers')
    seen=set();result=[]
    for pair in public:
        if not isinstance(pair,(list,tuple)) or len(pair)!=2:raise Refused('header_pair')
        name,value=pair
        if not isinstance(name,str) or not isinstance(value,str) or not value.isascii() or len(value)>1024 or any(ord(c)<32 or ord(c)==127 for c in value):raise Refused('header_value')
        key=name.lower()
        if key in seen or key not in ('content-type','anthropic-version','accept','anthropic-beta'):raise Refused('header_name_duplicate')
        seen.add(key);result.append([name,value])
    values={k.lower():v for k,v in result}
    if values.get('content-type')!='application/json' or values.get('anthropic-version')!='2023-06-01':raise Refused('required_headers')
    if 'accept' in values and values['accept'] not in ('application/json','text/event-stream'):raise Refused('accept')
    if 'anthropic-beta' in values and (len(values['anthropic-beta'])>519 or not 1<=len(values['anthropic-beta'].split(','))<=8 or any(not re.fullmatch('[a-z0-9][a-z0-9._-]{0,63}',x) for x in values['anthropic-beta'].split(','))):raise Refused('beta')
    auth=set()
    for name,value in private.items():
        if not isinstance(name,str) or not isinstance(value,str) or not 1<=len(value)<=4096 or any(ord(c)<32 or ord(c)==127 for c in value):raise Refused('private_header')
        key=name.lower()
        if key in auth or key not in ('x-api-key','authorization'):raise Refused('private_header_name')
        auth.add(key)
    if len(auth)!=1:raise Refused('one_supplied_auth_header')
    return result
