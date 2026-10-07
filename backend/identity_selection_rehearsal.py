"""Disposable SQLite durability rehearsal, not a production selection adapter.

All paths are created inside one owned TemporaryDirectory. There is no external
path, credential, network, installed-selector or consumer-enable interface.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile

import admission_operations as ops
import identity_selection_journal as journal


class Rehearsal:
    def __init__(self,root_record,prior_selections):
        journal.verify_chain([root_record])
        journal.require(type(prior_selections) is dict and set(prior_selections)==set(journal.HOSTS))
        for host in journal.HOSTS:
            journal.pointer(prior_selections[host])
            journal.require(journal.sha(prior_selections[host])==root_record['hosts'][host]['prior_sha256'])
        self.temporary=tempfile.TemporaryDirectory(prefix='ct-selection-rehearsal-')
        self.root=Path(self.temporary.name)
        try:
            with self.connect('journal') as db:
                db.execute('CREATE TABLE records (revision INTEGER PRIMARY KEY, body BLOB NOT NULL)')
                db.execute('INSERT INTO records VALUES (?,?)',(0,ops.encoded(root_record)))
            for host in journal.HOSTS:
                with self.connect(host) as db:
                    db.execute('CREATE TABLE selection (id INTEGER PRIMARY KEY CHECK(id=1), pointer BLOB NOT NULL, seen TEXT NOT NULL)')
                    db.execute('INSERT INTO selection VALUES (1,?,?)',(prior_selections[host],root_record['journal_sha256']))
        except BaseException:
            self.close()
            raise

    @contextmanager
    def connect(self,name):
        journal.require(name in ('journal',*journal.HOSTS))
        connection=sqlite3.connect(self.root/(name+'.sqlite'),timeout=2)
        try:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA journal_mode=DELETE')
            with connection: yield connection
        finally:
            connection.close()

    def close(self):
        self.temporary.cleanup()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    @staticmethod
    def history_from(connection):
        rows=connection.execute('SELECT revision,body FROM records ORDER BY revision').fetchall()
        journal.require([row[0] for row in rows]==list(range(len(rows))))
        history=[ops.strict_json(row[1]) for row in rows]
        journal.verify_chain(history)
        return history

    def history(self):
        with self.connect('journal') as db: return self.history_from(db)

    def read(self,host):
        journal.require(host in journal.HOSTS)
        with self.connect(host) as db:
            row=db.execute('SELECT pointer,seen FROM selection WHERE id=1').fetchone()
            journal.require(row is not None)
            return row

    def publish(self,host,*,expected_sha256,fault=lambda stage:None):
        """One synthetic pointer attempt. Every later call must acquire a new claim.

        A committed claim cannot be acquired again, even when no pointer changed.
        Each stage opens fresh connections to exercise persisted state.
        """
        journal.require(host in journal.HOSTS)
        with self.connect('journal') as db:
            db.execute('BEGIN IMMEDIATE')
            history=self.history_from(db)
            claimed=journal.claim(history[-1],host,expected_sha256=expected_sha256)
            db.execute('INSERT INTO records VALUES (?,?)',(claimed['revision'],ops.encoded(claimed)))
            fault('before_claim_commit')
        fault('after_claim_commit')
        with self.connect('journal') as central:
            central.execute('BEGIN IMMEDIATE')
            persisted=self.history_from(central)
            journal.require(len(persisted)>claimed['revision'] and persisted[claimed['revision']]==claimed)
            with self.connect(host) as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT pointer,seen FROM selection WHERE id=1').fetchone()
                journal.require(old is not None and journal.sha(old[0])==claimed['hosts'][host]['prior_sha256']
                    and old[1] in {item['journal_sha256'] for item in persisted})
                value=journal.pointer(old[0])
                target=ops.encoded({**value,'digest':claimed['target_digest'],
                    'operation':claimed['target_operation'],'sequence':value['sequence']+1})
                journal.require(journal.sha(target)==claimed['hosts'][host]['target_sha256'])
                count=db.execute('UPDATE selection SET pointer=?,seen=? WHERE id=1 AND pointer=?',
                    (target,persisted[-1]['journal_sha256'],old[0])).rowcount
                journal.require(count==1)
                fault('before_pointer_commit')
        fault('after_pointer_commit')
        raw,_=self.read(host)
        journal.require(raw==target)
        return 'synthetic_pointer_readback_only'

    def deliver(self,host):
        """Rehearse verified journal propagation; never publish a pointer."""
        journal.require(host in journal.HOSTS)
        with self.connect('journal') as central:
            central.execute('BEGIN IMMEDIATE')
            history=self.history_from(central)
            with self.connect(host) as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT seen FROM selection WHERE id=1').fetchone()
                journal.require(row is not None and row[0] in {item['journal_sha256'] for item in history})
                db.execute('UPDATE selection SET seen=? WHERE id=1',(history[-1]['journal_sha256'],))

    def assess(self):
        history=self.history(); observed={}
        for host in journal.HOSTS:
            raw,seen=self.read(host)
            observed[host]=dict(journal_sha256=seen,selection_sha256=journal.sha(raw))
        return journal.assess(history[-1],observed,history=history)

    def consume(self):
        self.assess()
        raise PermissionError('Rehearsal consumers remain disabled.')
