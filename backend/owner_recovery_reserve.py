"""Single pre-enrolled reserve slot; no launcher or enrollment on import.

Host adapter is independently reviewed: enrollment_view() and successor_view()
hold host/DB guards only for short snapshots. transaction_view(connection, op)
uses the SAME attestation transaction. Every successor view returns bridge and
lineage records; no caller-supplied booleans substitute for authoritative reads.
Lock order: primary credential, reserve credential, host, database.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import os
from pathlib import Path
import time
import owner_recovery as recovery

ROOT=Path('/var/lib/careertrellis-owner-recovery-reserve')
require=recovery.require

def sha(raw):return hashlib.sha256(raw).hexdigest()
def anchor(credential):
    return sha(recovery.encoded({k:credential[k] for k in ('id','binding','created','confirmed','salt','hash')}))

class Store(recovery.Store):
    def __init__(self):super().__init__(ROOT)
    def read_metadata(self):return self.immutable('enrollment.json')
    def immutable(self,name,value=None):
        require(name in ('enrollment.json','issued.json','issuance-claim.json'))
        path=self.root/name
        if value is None:return recovery.strict_json(recovery.protected(path))
        raw=recovery.encoded(value);recovery.parents(path)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        fd=os.open(self.root,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def replace_pending(self,*args,**kwargs):raise ValueError(recovery.ERROR)
    def authenticate(self,code,binding,now,*,confirm=False,operation=None):
        require(confirm is True and operation is None)
        return super().authenticate(code,binding,now,confirm=True)
    def new_generation(self,*args,**kwargs):
        require(getattr(self,'_issuing',False))
        return super().new_generation(*args,**kwargs)
    def issue_once(self,binding,now):
        meta=self.read_metadata()
        require(self.read() is None and meta['binding']==binding
            and not (self.root/'issued.json').exists() and not (self.root/'issued.json').is_symlink())
        self.immutable('issuance-claim.json',{'enrollment_sha256':sha(recovery.encoded(meta)),'started':now})
        self._issuing=True
        try:return super().issue(binding,now)
        finally:self._issuing=False

def enrollment(primary,view):
    require(type(view) is dict and set(view)=={'binding','original_operation','original_selection','original_plan',
        'prehold_verified'})
    require(view['prehold_verified'] is True and primary['version']==1 and primary['state']=='active'
        and primary['binding']==view['binding'])
    require(all(type(view[n]) is bytes and 0<len(view[n])<=65536 for n in ('original_selection','original_plan')))
    require(len(view['original_selection'])+len(view['original_plan'])<=24000)
    from uuid import UUID
    require(str(UUID(view['original_operation']))==view['original_operation'])
    return {'version':1,'primary_id':primary['id'],'primary_anchor':anchor(primary),'binding':primary['binding'],
        'original_operation':view['original_operation'],'original_selection':sha(view['original_selection']),
        'original_plan':sha(view['original_plan']),
        'original_selection_hex':view['original_selection'].hex(),'original_plan_hex':view['original_plan'].hex()}

def eligible(meta,issued,primary,reserve,view,now):
    require(type(now) is int and type(view) is dict and set(view)=={'bridge','lineage'})
    require(primary['version']==2 and primary['state']=='consumed' and primary['id']==meta['primary_id'])
    require(anchor(primary)==meta['primary_anchor'])
    receipt=recovery.transition_receipt(primary['receipt'],primary)
    old=receipt['context'];started,deadline=recovery.context_times(old)
    current=view['bridge'];new=current['context'];recovery.context_times(new)
    lineage=view['lineage']
    require(type(lineage) is dict and set(lineage)=={'predecessor_context','predecessor_state',
        'original_selection','original_plan','primary_selection','successor_context','successor_selection',
        'uncommitted_verified','acknowledgement_resolved','successor_ready_verified'})
    require(lineage['predecessor_context']==old and lineage['predecessor_state']=='superseded'
        and lineage['successor_context']==new and lineage['uncommitted_verified'] is True
        and lineage['acknowledgement_resolved'] is True and lineage['successor_ready_verified'] is True)
    require(old['operation']==meta['original_operation'] and old['predecessor'] is None
        and receipt['selection']==sha(lineage['primary_selection'])
        and meta['original_selection']==sha(lineage['original_selection'])
        and meta['original_plan']==sha(lineage['original_plan'])
        and current['selection']==sha(lineage['successor_selection'])
        and new['predecessor']==old['operation'] and new['epoch']==old['epoch']+1)
    require(all(new[k]==old[k] for k in ('account_id','incarnation','old_binding','old_subject',
        'target_binding','target_subject','expected_role','owner_authorized')))
    require(now>=receipt['expires'] and now>=deadline and current['schema']==receipt['schema']=='20260929_0021')
    require(reserve['id']==issued['reserve_id'] and reserve['binding']==primary['binding']==meta['binding']==current['binding']
        and reserve['created']<=reserve['confirmed']<=started and reserve['confirmed']>0)
    require(issued['enrollment_sha256']==sha(recovery.encoded(meta)))
    return current

class Coordinator:
    def __init__(self,host,*,primary=None,reserve=None,now=lambda:int(time.time())):
        self.host=host;self.primary=primary or recovery.Store();self.reserve=reserve or Store();self.now=now
        require(self.primary.root==recovery.STATE and self.reserve.root==ROOT)
    @contextmanager
    def locked(self):
        with self.primary.locked(),self.reserve.locked():
            original=recovery.protected(self.primary.path)
            try:yield
            finally:require(recovery.protected(self.primary.path)==original)
    def enroll(self,terminal):
        with self.locked():
            with self.host.enrollment_view() as view:
                meta=enrollment(self.primary.read(),view)
                self.reserve.immutable('enrollment.json',meta)
                code=self.reserve.issue_once(meta['binding'],self.now())
                self.reserve.immutable('issued.json',{'reserve_id':self.reserve.read()['id'],
                    'enrollment_sha256':sha(recovery.encoded(meta))})
            terminal.show('Save this separately enrolled, single-use reserve credential:\n'+code+'\n')
            code=None
        return {'outcome':'reserve_pending_confirmation'}
    def confirm(self,terminal):
        with self.locked():
            meta=self.reserve.immutable('enrollment.json');issued=self.reserve.immutable('issued.json')
            with self.host.enrollment_view() as view:require(enrollment(self.primary.read(),view)==meta)
            code=terminal.ask('Paste the saved reserve credential to confirm custody')
            with self.host.enrollment_view() as view:
                require(enrollment(self.primary.read(),view)==meta and self.reserve.read()['id']==issued['reserve_id'])
                self.reserve.authenticate(code,meta['binding'],self.now(),confirm=True)
            code=None
        return {'outcome':'reserve_confirmed_not_consumed'}
    def checked(self,view):
        return eligible(self.reserve.immutable('enrollment.json'),self.reserve.immutable('issued.json'),
            self.primary.read(),self.reserve.read(),view,self.now())
    def consume(self,operation,terminal):
        import owner_recovery_bridge as bridge
        with self.locked():
            with self.host.successor_view(operation) as view:initial=self.checked(view)
            require(terminal.ask('Type RESERVE-SUCCESSOR to consume the one reserve')=='RESERVE-SUCCESSOR')
            code=terminal.ask('Single-use reserve credential; no replacement is available')
            with self.host.successor_view(operation) as view:
                current=self.checked(view);require(current==initial)
                bridge.consume(self.reserve,code,current,self.now());code=None
                receipt=bridge.verify(self.reserve,current,self.now())
            require(receipt['issued']<=self.now()<receipt['expires'])
        return {'outcome':'reserve_successor_holder_verified'}
    def receipt(self,operation):
        """Display-only timing; never a transferable authentication receipt."""
        import owner_recovery_bridge as bridge
        with self.locked():
            with self.host.successor_view(operation) as view:
                current=self.checked(view);receipt=bridge.verify(self.reserve,current,self.now())
                result={'outcome':'reserve_holder_window_display_only',
                    'issued_at':datetime.fromtimestamp(receipt['issued'],timezone.utc).isoformat(),
                    'expires_at':datetime.fromtimestamp(receipt['expires'],timezone.utc).isoformat()}
            require(receipt['issued']<=self.now()<receipt['expires'])
        return result
    def enter(self,operation,terminal):
        import owner_recovery_bridge as bridge
        path,journal=bridge.operator.locations(operation)
        with self.locked():
            with self.host.successor_view(operation) as view:
                initial=self.checked(view);bridge.verify(self.reserve,initial,self.now())
                require(not path.exists() and not path.is_symlink() and not journal.exists() and not journal.is_symlink())
            def holder():
                with self.host.successor_view(operation) as view:
                    current=self.checked(view);require(current==initial)
                    return bridge.verify(self.reserve,current,self.now())
            raw=bridge.identity_entry.build_with_verified_holder_receipt(initial['context'],terminal.ask,
                verify_holder=holder,now=lambda:datetime.fromtimestamp(self.now(),timezone.utc))
            with self.host.successor_view(operation) as view:
                current=self.checked(view);require(current==initial)
                receipt=bridge.verify(self.reserve,current,self.now());packet=bridge.identity_entry.evidence.validate(raw)
                packet_expires=bridge.identity_entry.evidence.instant(packet['expires_at']).timestamp()
                require(self.now()<packet_expires<=receipt['expires'])
                require(not path.exists() and not path.is_symlink() and not journal.exists() and not journal.is_symlink())
                bridge.ops.write_protected(path,raw)
            require(receipt['issued']<=self.now()<min(receipt['expires'],packet_expires))
        return {'outcome':'reserve_manifest_saved_not_attested'}
    def attest(self,operation,source,engine):
        import owner_recovery_bridge as bridge
        with self.locked(),self.host.locks():
            def submit(raw,target):
                def guard(connection,packet):
                    with self.host.transaction_view(connection,operation) as view:
                        current=self.checked(view);receipt=bridge.verify(self.reserve,current,self.now())
                        bridge.identity_entry.evidence._same_context(current['context'],packet['context'])
                        require(packet['version']==2 and bridge.identity_entry.evidence.instant(
                            packet['holder']['completed_at']).timestamp()==receipt['issued'])
                        require(self.now()<bridge.identity_entry.evidence.instant(packet['expires_at']).timestamp()<=receipt['expires'])
                return bridge.identity_entry.evidence.submit_with_holder_guard(raw,target,guard)
            return bridge.operator.process('attest',operation,source,engine,submit=submit)
    def historical(self,action,operation,source,engine):
        """Exact old acknowledgement only; expiry never becomes renewed authority."""
        import owner_recovery_bridge as bridge
        require(action in ('status','reconcile'))
        with self.locked(),bridge.ops.host_locks():
            meta=self.reserve.read_metadata();issued=self.reserve.immutable('issued.json')
            primary=self.primary.read();value=self.reserve.read()
            require(primary['version']==2 and primary['state']=='consumed'
                and primary['id']==meta['primary_id'] and anchor(primary)==meta['primary_anchor'])
            old=recovery.transition_receipt(primary['receipt'],primary)
            require(value['version']==2 and value['state']=='consumed' and value['id']==issued['reserve_id']
                and issued['enrollment_sha256']==sha(recovery.encoded(meta)))
            receipt=recovery.transition_receipt(value['receipt'],value)
            primary_selection=self.host.primary_selection()
            require(old['operation']==meta['original_operation'] and old['selection']==sha(primary_selection)
                and receipt['operation']==operation and receipt['context']['predecessor']==old['operation']
                and receipt['context']['epoch']==old['context']['epoch']+1)
            require(all(receipt['context'][key]==old['context'][key] for key in ('account_id','incarnation',
                'old_binding','old_subject','target_binding','target_subject','expected_role','owner_authorized')))
            require(value['confirmed']<=recovery.context_times(old['context'])[0]
                and value['binding']==primary['binding']==meta['binding'])
            def never_submit(*args,**kwargs):raise ValueError(recovery.ERROR)
            return bridge.operator.process(action,operation,source,engine,submit=never_submit)
