"""Operator-started guided entry only; never invoked by a background agent.

All input is hidden and bounded. Writes an outcome manifest, never an attestation
or identity change. TTY checks cannot prove that a human can see the terminal.
"""
from datetime import datetime, timezone, timedelta
import os
import select
import stat
import time

import admission_operations as ops
import identity_evidence as evidence
import identity_operator as operator


class SecureTerminal:
    def __enter__(self):
        ops.require(os.name=='posix' and all(os.isatty(fd) for fd in (0,1,2)))
        import termios
        import signal
        self.fd=os.open('/dev/tty',os.O_RDWR|os.O_NOCTTY)
        self.original=None; self.handlers={}
        try:
            ops.require(os.tcgetpgrp(self.fd)==os.getpgrp())
            def cancel(signum,frame): raise InterruptedError('Operator entry cancelled.')
            for signum in (signal.SIGTERM,signal.SIGHUP,signal.SIGTSTP,signal.SIGINT,signal.SIGTTIN,signal.SIGTTOU):
                self.handlers[signum]=signal.getsignal(signum)
                signal.signal(signum,cancel)
            self.original=termios.tcgetattr(self.fd)
            hidden=termios.tcgetattr(self.fd)
            hidden[3] &= ~(termios.ECHO|termios.ECHONL|termios.ICANON)
            hidden[6][termios.VMIN]=1; hidden[6][termios.VTIME]=0
            termios.tcsetattr(self.fd,termios.TCSAFLUSH,hidden)
            self.deadline=time.monotonic()+300
            return self
        except BaseException:
            self.restore()
            raise

    def __exit__(self,*args):
        self.restore()

    def restore(self):
        import termios
        import signal
        previous=signal.pthread_sigmask(signal.SIG_BLOCK,set(self.handlers))
        try:
            try:
                if self.original is not None: termios.tcsetattr(self.fd,termios.TCSAFLUSH,self.original)
            finally:
                for signum,handler in self.handlers.items(): signal.signal(signum,handler)
                os.close(self.fd)
        finally: signal.pthread_sigmask(signal.SIG_SETMASK,previous)

    def ask(self,prompt,*,limit=256):
        # Prompts are fixed strings from this module, never database values.
        ops.require(type(limit) is int and 1<=limit<=512)
        os.write(self.fd,(prompt+' (hidden): ').encode())
        deadline=min(self.deadline,time.monotonic()+120); value=bytearray(); pasted=False
        def read_char():
            remaining=deadline-time.monotonic()
            ops.require(remaining>0 and select.select([self.fd],[],[],remaining)[0])
            char=os.read(self.fd,1)
            ops.require(bool(char))
            return char
        while True:
            char=read_char()
            if char==b'\x1b':
                marker=b''.join(read_char() for _ in range(5))
                ops.require(marker==(b'[201~' if pasted else b'[200~'))
                pasted=not pasted
                continue
            if char in (b'\n',b'\r'):
                ops.require(not pasted)
                os.write(self.fd,b'\n')
                return value.decode('ascii')
            if char in (b'\x7f',b'\x08'):
                ops.require(not pasted)
                if value: value.pop()
                continue
            ops.require(32<=char[0]<=126 and len(value)<limit)
            value.extend(char)


def context(connection,operation):
    from sqlalchemy import text
    value=connection.scalar(text('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))'),{'op':operation})
    ready=connection.scalar(text("SELECT EXISTS(SELECT 1 FROM careertrellis_admission.identity_transitions p "
        "JOIN careertrellis_admission.identity_accounts a ON a.id=p.account_id WHERE p.operation=CAST(:op AS uuid) "
        "AND p.state='held' AND p.deadline>clock_timestamp() AND a.held AND NOT a.disabled "
        "AND NOT careertrellis_admission.identity_erasing(a.id) AND a.epoch=p.epoch AND a.incarnation=p.incarnation "
        "AND a.role=p.expected_role AND a.binding=p.old_binding AND a.subject=p.old_subject)"),{'op':operation})
    ops.require(ready is True)
    return value


def build(value,ask,*,now=lambda:datetime.now(timezone.utc)):
    return _build(value,ask,now=now)


def build_with_verified_holder_receipt(value,ask,*,verify_holder,now=lambda:datetime.now(timezone.utc)):
    """Operator-verified v2 evidence, NOT authentication of an imported JSON file.

    The trusted caller must verify the source receipt against the protected
    consumed credential, current selected plan and held DB context. The callback
    runs again after human input; changed/expired receipts never become packets.
    """
    from owner_recovery import TRANSITION_SCOPE
    receipt=verify_holder()
    ops.require(type(receipt) is dict and receipt['scope']==TRANSITION_SCOPE
        and receipt['schema'] in operator.HEADS and type(receipt['issued']) is int
        and type(receipt['expires']) is int)
    evidence._same_context(value,receipt['context'])
    start=datetime.fromtimestamp(receipt['issued'],timezone.utc)
    expiry=datetime.fromtimestamp(receipt['expires'],timezone.utc)
    ops.require(start<=now()<expiry)
    raw=_build(value,ask,now=now,holder=start.isoformat(),holder_expires=expiry)
    ops.require(verify_holder()==receipt and now()<expiry)
    packet=evidence.validate(raw)
    ops.require(packet['holder']['completed_at']==start.isoformat()
        and evidence.instant(packet['expires_at'])<=expiry)
    return raw


def _build(value,ask,*,now,holder=None,holder_expires=None):
    """Confirm scope independently; never display or request raw holder proof."""
    ops.require(ask('Confirm you started this in your visible secure terminal: type VISIBLE')=='VISIBLE')
    for key,label in (('account_id','Existing local account ID'),('incarnation','Account incarnation UUID'),
            ('epoch','Held account epoch'),('old_binding','Verified source provider binding'),
            ('old_subject','Verified source WorkOS user ID'),('target_binding','Verified target provider binding'),
            ('target_subject','Verified target WorkOS user ID')):
        ops.require(ask(label)==str(value[key]))
    if holder is None:
        ops.require(ask('Independent holder verification completed under recovery policy, not email matching: type VERIFIED')=='VERIFIED')
        holder=ask('Holder verification completed at, UTC ISO timestamp')
    ops.require(ask('Fresh target authentication completed on final domain: type AUTHENTICATED')=='AUTHENTICATED')
    target=ask('Target authentication completed at, UTC ISO timestamp')
    method=ask('Verified authentication method: google, password or passkey')
    ops.require(ask('Target email verified by provider: type EMAIL-VERIFIED')=='EMAIL-VERIFIED')
    factor_status=ask('Independently verified applicable factor status: none_enrolled or enforced; Google alone is not MFA proof')
    ops.require(ask('All source and target provider sessions revoked after verification: type REVOKED')=='REVOKED')
    revoked=ask('Provider revocation completed at, UTC ISO timestamp')
    approval=None
    if value['expected_role']=='owner':
        ops.require(ask('Separately authorized this owner operation and verified exact target owner configuration: type OWNER-AUTHORIZED')=='OWNER-AUTHORIZED')
        approval={k:value[k] for k in ('operation','account_id','epoch','target_binding','target_subject')}
        approval.update(configuration_verified=True,authorized_at=ask('Owner authorization completed at, UTC ISO timestamp'))
    verified=now()
    # Evidence window is distinct from the approved 3600-second session lifetime.
    expires=min(verified+timedelta(minutes=5),evidence.instant(value['deadline']))
    if holder_expires is not None:
        ops.require(verified<holder_expires)
        expires=min(expires,holder_expires)
    packet={'version':2,'context':value,'holder':{'method':'independent_assisted_verification','completed_at':holder},
        'target':{'method':'fresh_authentication_under_optional_mfa','completed_at':target,
            'authentication_method':method,'email_verified':True,'applicable_factor_status':factor_status},
        'revocation':{'method':'all_source_and_target_provider_sessions','completed_at':revoked},
        'verified_at':verified.isoformat(),'expires_at':expires.isoformat(),'owner_approval':approval}
    raw=ops.encoded(packet)
    evidence.validate(raw)
    ops.require(ask('Save outcome manifest only; no database submission: type SAVE')=='SAVE')
    return raw


def enter(operation,engine):
    # Finish filesystem, principal, schema, logging and exact operation preflight
    # before opening a terminal. Never keep a DB transaction across human input.
    path,journal=operator.locations(operation)
    ops.protected_parents(ops.STATE/'sentinel')
    directory=ops.STATE.lstat()
    ops.require(stat.S_ISDIR(directory.st_mode) and directory.st_uid==0 and stat.S_IMODE(directory.st_mode)==0o700)
    ops.require(not path.exists() and not path.is_symlink() and not journal.exists() and not journal.is_symlink())
    with operator.secure_connection(engine) as connection:
        operator.setup(connection)
        initial=context(connection,operation)
    with SecureTerminal() as terminal:
        raw=build(initial,terminal.ask)
    # Recheck mutable state after the human pause, under both host and DB locks.
    with ops.host_locks(), operator.secure_connection(engine) as connection:
        operator.setup(connection)
        evidence._same_context(initial,context(connection,operation))
        ops.require(not journal.exists() and not journal.is_symlink())
        ops.require(evidence.instant(evidence.validate(raw)['expires_at'])>datetime.now(timezone.utc))
        ops.write_protected(path,raw)
    return {'outcome':'manifest_saved_not_submitted'}
