"""Unwired transactional identity/epoch candidate for isolated SQLite fixtures.

No application DB imports, provider calls, CLI, default paths or auto-migration.
Proof objects are synthetic trusted-operator assertions, not holder verification.
PostgreSQL RLS, runtime wiring and protected evidence intake remain separate gates.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import re
import sqlite3
from uuid import UUID


class TransitionRejected(Exception):
    def __init__(self):
        super().__init__('Identity transition unavailable.')


def require(value):
    if not value:
        raise TransitionRejected()


def epoch(value):
    return type(value) is int and 1 <= value < 9223372036854775807


@dataclass(frozen=True, repr=False)
class Identity:
    # Binding is the exact verified provider environment/client/issuer contract.
    binding: str
    subject: str

    def __post_init__(self):
        require(isinstance(self.binding, str) and re.fullmatch('[0-9a-f]{64}', self.binding))
        require(isinstance(self.subject, str) and re.fullmatch('user_[A-Za-z0-9_]{1,120}', self.subject))


@dataclass(frozen=True, repr=False)
class Grant:
    local_id: int
    identity: Identity
    epoch: int


@dataclass(frozen=True, repr=False)
class Evidence:
    operation: str
    local_id: int
    old: Identity
    target: Identity
    epoch: int
    verified_at: int
    expires: int
    holder_verified: bool
    target_authenticated: bool
    factors_enrolled: bool
    provider_sessions_revoked: bool
    owner_authorized: bool = False


SCHEMA = '''
CREATE TABLE candidate_accounts(id INTEGER PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('user','owner')),
 epoch INTEGER NOT NULL CHECK(epoch>0), disabled INTEGER NOT NULL DEFAULT 0,
 erasing INTEGER NOT NULL DEFAULT 0, held INTEGER NOT NULL DEFAULT 0);
CREATE TABLE candidate_owned_data(account_id INTEGER REFERENCES candidate_accounts(id), value TEXT NOT NULL);
CREATE TABLE candidate_identities(binding TEXT NOT NULL, subject TEXT NOT NULL,
 account_id INTEGER NOT NULL REFERENCES candidate_accounts(id), state TEXT NOT NULL CHECK(state IN ('active','pending','retired')),
 PRIMARY KEY(binding,subject));
CREATE UNIQUE INDEX candidate_one_active ON candidate_identities(account_id) WHERE state='active';
CREATE TABLE candidate_transitions(operation TEXT PRIMARY KEY, parameters TEXT NOT NULL,
 account_id INTEGER NOT NULL REFERENCES candidate_accounts(id), epoch INTEGER NOT NULL,
 started INTEGER NOT NULL, deadline INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('held','verified','committed')),
 evidence TEXT, expected_role TEXT NOT NULL);
'''


def initialize_empty_fixture(connection):
    require(not connection.in_transaction)
    require(not connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    connection.executescript(SCHEMA)
    connection.commit()


class IdentityLedger:
    def __init__(self, connection):
        require(isinstance(connection, sqlite3.Connection) and not connection.in_transaction)
        self.db = connection
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=1000')
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(names == {'candidate_accounts','candidate_owned_data','candidate_identities','candidate_transitions'})

    @contextmanager
    def transaction(self):
        require(not self.db.in_transaction)
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.commit()
        except (sqlite3.Error, TransitionRejected):
            self.db.rollback()
            raise TransitionRejected() from None
        except BaseException:
            self.db.rollback()
            raise

    def _account(self, local_id):
        require(type(local_id) is int and local_id > 0)
        row = self.db.execute('SELECT * FROM candidate_accounts WHERE id=?',(local_id,)).fetchone()
        require(row is not None)
        return row

    def _active(self, local_id, identity):
        require(isinstance(identity, Identity))
        row = self.db.execute("SELECT 1 FROM candidate_identities WHERE account_id=? AND binding=? AND subject=? AND state='active'",
            (local_id, identity.binding, identity.subject)).fetchone()
        require(row is not None)

    def _check(self, grant):
        require(isinstance(grant, Grant) and epoch(grant.epoch))
        row = self._account(grant.local_id)
        require(not row['disabled'] and not row['erasing'] and not row['held'] and row['epoch'] == grant.epoch)
        self._active(grant.local_id, grant.identity)
        return row

    def capture(self, local_id, identity):
        with self.transaction():
            grant = Grant(local_id, identity, self._account(local_id)['epoch'])
            self._check(grant)
            return grant

    def check(self, grant):
        with self.transaction():
            return dict(self._check(grant))

    def protected_write(self, grant, value):
        # The same transaction serializes authorization and the protected effect.
        with self.transaction():
            self._check(grant)
            self.db.execute('INSERT INTO candidate_owned_data VALUES(?,?)',(grant.local_id,value))

    def prepare(self, operation, grant, target, *, now, deadline, owner_authorized=False):
        try:
            require(isinstance(operation,str) and str(UUID(operation)) == operation)
        except (ValueError, AttributeError):
            raise TransitionRejected() from None
        require(isinstance(grant,Grant) and isinstance(grant.identity,Identity) and isinstance(target,Identity))
        require(type(grant.local_id) is int and grant.local_id > 0 and epoch(grant.epoch))
        require(type(now) is int and type(deadline) is int and 0 <= now < deadline <= now+3600)
        require(type(owner_authorized) is bool)
        parameters = json.dumps([grant.local_id,grant.identity.binding,grant.identity.subject,
            grant.epoch,target.binding,target.subject,deadline,owner_authorized],separators=(',',':'))
        with self.transaction():
            previous = self.db.execute('SELECT * FROM candidate_transitions WHERE operation=?',(operation,)).fetchone()
            if previous:
                require(previous['parameters'] == parameters)
                return previous['state']
            row = self._check(grant)
            require(row['role'] != 'owner' or owner_authorized)
            require(epoch(grant.epoch+1))
            if target != grant.identity:
                require(not self.db.execute('SELECT 1 FROM candidate_identities WHERE binding=? AND subject=?',
                    (target.binding,target.subject)).fetchone())
                self.db.execute("INSERT INTO candidate_identities VALUES(?,?,?,'pending')",
                    (target.binding,target.subject,grant.local_id))
            self.db.execute('UPDATE candidate_accounts SET held=1,epoch=epoch+1 WHERE id=?',(grant.local_id,))
            self.db.execute("INSERT INTO candidate_transitions VALUES(?,?,?,?,?,?,'held',NULL,?)",
                (operation,parameters,grant.local_id,grant.epoch+1,now,deadline,row['role']))
            return 'held'

    def attest(self, evidence, *, now):
        require(isinstance(evidence,Evidence) and type(now) is int)
        require(type(evidence.local_id) is int and evidence.local_id > 0 and epoch(evidence.epoch))
        require(isinstance(evidence.old,Identity) and isinstance(evidence.target,Identity))
        require(all(value is True for value in (evidence.holder_verified,evidence.target_authenticated,
            evidence.factors_enrolled,evidence.provider_sessions_revoked)))
        require(type(evidence.verified_at) is int and type(evidence.expires) is int)
        with self.transaction():
            op = self.db.execute('SELECT * FROM candidate_transitions WHERE operation=?',(evidence.operation,)).fetchone()
            require(op is not None and op['state'] in ('held','verified'))
            p = json.loads(op['parameters'])
            require([evidence.local_id,evidence.old.binding,evidence.old.subject,evidence.epoch-1,
                evidence.target.binding,evidence.target.subject] == p[:6])
            require(epoch(evidence.epoch) and evidence.epoch == op['epoch'])
            require(op['started'] <= evidence.verified_at <= now < evidence.expires <= op['deadline'])
            row = self._account(op['account_id'])
            require(row['role'] == op['expected_role'])
            require(row['held'] and not row['disabled'] and not row['erasing'] and row['epoch'] == op['epoch'])
            require(row['role'] != 'owner' or evidence.owner_authorized is True and p[7] is True)
            encoded = json.dumps([evidence.verified_at,evidence.expires],separators=(',',':'))
            if op['state'] == 'verified':
                require(op['evidence'] == encoded)
            self.db.execute("UPDATE candidate_transitions SET state='verified',evidence=? WHERE operation=?",
                (encoded,evidence.operation))

    def activate(self, operation, *, now):
        require(type(now) is int)
        with self.transaction():
            op = self.db.execute('SELECT * FROM candidate_transitions WHERE operation=?',(operation,)).fetchone()
            require(op is not None)
            # A replay reports the historical commit; it never clears a later hold.
            if op['state'] == 'committed':
                return {'state':'committed','epoch':op['epoch']}
            require(op['state'] == 'verified' and json.loads(op['evidence'])[0] <= now < json.loads(op['evidence'])[1])
            p = json.loads(op['parameters'])
            row = self._account(op['account_id'])
            require(row['role'] == op['expected_role'])
            require(row['held'] and not row['disabled'] and not row['erasing'] and row['epoch'] == op['epoch'])
            old, target = Identity(p[1],p[2]), Identity(p[4],p[5])
            self._active(row['id'],old)
            if target != old:
                self.db.execute("UPDATE candidate_identities SET state='retired' WHERE account_id=? AND state='active'",(row['id'],))
                changed = self.db.execute("UPDATE candidate_identities SET state='active' WHERE account_id=? AND binding=? AND subject=? AND state='pending'",
                    (row['id'],target.binding,target.subject))
                require(changed.rowcount == 1)
            self.db.execute('UPDATE candidate_accounts SET held=0 WHERE id=?',(row['id'],))
            self.db.execute("UPDATE candidate_transitions SET state='committed' WHERE operation=?",(operation,))
            return {'state':'committed','epoch':op['epoch']}
