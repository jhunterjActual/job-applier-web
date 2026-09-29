"""Pure in-memory source packet checks; no private files or execution authority."""
import hashlib
import json
import unittest

import factor_release_packet as packet


BASE='a'*40


def migration(revision,parent):
    return ('revision = '+repr(revision)+'\ndown_revision = '+repr(parent)+
            '\nbranch_labels = None\ndepends_on = None\n').encode()


def fixture():
    files={name:b'# synthetic source\n' for name in packet.REQUIRED}
    files['backend/version.py']=b'APP_BUILD = "20260928.78"\n'
    files['backend/migrations/versions/20260907_0014_synthetic.py']=migration('20260907_0014',None)
    previous='20260907_0014'
    for revision in packet.CHAIN:
        name='backend/migrations/versions/'+revision+'_synthetic'
        files[name+'.py']=migration(revision,previous)
        files[name+'.sql']=b'-- synthetic migration\n'
        previous=revision
    return files


class FactorReleasePacketTests(unittest.TestCase):
    def setUp(self): self.files=fixture()

    def test_manifest_binds_every_supplied_byte_and_canonical_order(self):
        result=packet.source_packet(self.files,base=BASE)
        manifest={name:hashlib.sha256(raw).hexdigest() for name,raw in self.files.items()}
        self.assertEqual(manifest,result['source_manifest'])
        digest=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(digest,result['source_manifest_sha256'])
        self.assertEqual(sum(map(len,self.files.values())),result['source_bytes'])
        reversed_files=dict(reversed(list(self.files.items())))
        self.assertEqual(result,packet.source_packet(reversed_files,base=BASE))

    def test_changed_source_and_extra_source_change_manifest_binding(self):
        original=packet.source_packet(self.files,base=BASE)
        self.files['backend/factor_removal.py']+=b'# changed\n'
        changed=packet.source_packet(self.files,base=BASE)
        self.assertNotEqual(original['source_manifest_sha256'],changed['source_manifest_sha256'])
        self.files['backend/extra_synthetic.py']=b'# extra\n'
        extra=packet.source_packet(self.files,base=BASE)
        self.assertNotEqual(changed['source_manifest_sha256'],extra['source_manifest_sha256'])
        self.assertIn('backend/extra_synthetic.py',extra['source_manifest'])

    def test_each_required_runtime_test_or_contract_file_is_required(self):
        for name in packet.REQUIRED:
            with self.subTest(name=name):
                files=self.files.copy(); del files[name]
                with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_each_schema_link_and_sql_companion_is_required(self):
        for revision in packet.CHAIN:
            for suffix in ('.py','.sql'):
                name='backend/migrations/versions/'+revision+'_synthetic'+suffix
                with self.subTest(name=name):
                    files=self.files.copy(); del files[name]
                    with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_wrong_parent_for_each_revision_rejected(self):
        for revision in packet.CHAIN:
            files=self.files.copy()
            files['backend/migrations/versions/'+revision+'_synthetic.py']=migration(revision,'20260901_wrong')
            with self.subTest(revision=revision):
                with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_parallel_or_future_child_of_reviewed_chain_is_rejected(self):
        for parent in packet.CHAIN:
            files=self.files.copy()
            files['backend/migrations/versions/extra_branch.py']=migration('20260930_0021',parent)
            files['backend/migrations/versions/extra_branch.sql']=b'-- synthetic branch\n'
            with self.subTest(parent=parent):
                with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_duplicate_revision_and_duplicate_assignments_rejected(self):
        files=self.files.copy()
        files['backend/migrations/versions/duplicate.py']=migration(packet.CHAIN[0],'20260907_0014')
        files['backend/migrations/versions/duplicate.sql']=b'-- duplicate\n'
        with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)
        files=self.files.copy()
        name='backend/migrations/versions/'+packet.CHAIN[0]+'_synthetic.py'
        files[name]+=b'revision = "duplicate"\n'
        with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)
        self.files['backend/version.py']+=b'APP_BUILD = "20260929.79"\n'
        with self.assertRaises(ValueError): packet.source_packet(self.files,base=BASE)

    def test_annotated_migration_metadata_is_accepted(self):
        files=self.files.copy()
        for name,raw in list(files.items()):
            if name.startswith('backend/migrations/versions/') and name.endswith('.py'):
                files[name]=raw.replace(b'down_revision =',b'down_revision: str | None =').replace(
                    b'revision =',b'revision: str =')
        result=packet.source_packet(files,base=BASE)
        self.assertEqual(packet.SCHEMA,result['schema'])
        self.assertIs(result['execution_authorized'],False)

    def test_mixed_annotated_and_plain_duplicate_metadata_is_rejected(self):
        name='backend/migrations/versions/'+packet.CHAIN[0]+'_synthetic.py'
        for assignment in (b'revision: str = "duplicate"\n',
                           b'down_revision: str | None = "20260907_0014"\n'):
            files=self.files.copy(); files[name]+=assignment
            with self.subTest(assignment=assignment):
                with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_sibling_of_predecessor_missing_parent_and_merge_rejected(self):
        for revision,parent in (('sibling','20260907_0014'),('unlinked','missing_parent'),
                                ('merge',('20260907_0014',packet.SCHEMA))):
            files=self.files.copy()
            files['backend/migrations/versions/additional.py']=migration(revision,parent)
            with self.subTest(revision=revision):
                with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)
        files=self.files.copy(); del files['backend/migrations/versions/20260907_0014_synthetic.py']
        with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_disconnected_second_root_and_cycle_rejected(self):
        files=self.files.copy()
        files['backend/migrations/versions/second_root.py']=migration('second_root',None)
        with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)
        files=self.files.copy()
        files['backend/migrations/versions/cycle_a.py']=migration('cycle_a','cycle_b')
        files['backend/migrations/versions/cycle_b.py']=migration('cycle_b','cycle_a')
        with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_protected_absolute_traversal_and_noncanonical_paths_rejected(self):
        for name in ('../outside','/absolute','backend/../outside','backend/./outside',
                     'backend//outside','backend\\outside','C:/outside','backend/.env',
                     'backend/.env.secret','data/private','backend/venv/bin/python',
                     '.git/config','.codex/config.toml','backend/__pycache__/module.pyc','backend/'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError): packet.source_packet({**self.files,name:b'forbidden'},base=BASE)

    def test_file_parent_collision_rejected_and_public_env_example_allowed(self):
        with self.assertRaises(ValueError): packet.source_packet({**self.files,'backend':b'file'},base=BASE)
        result=packet.source_packet({**self.files,'backend/.env.example':b'# public synthetic template\n'},base=BASE)
        self.assertIn('backend/.env.example',result['source_manifest'])

    def test_source_types_empty_and_per_file_size_limit(self):
        for bad in ('text instead of bytes',b'',b'x'*10000001):
            files=self.files.copy(); files['backend/factor_removal.py']=bad
            with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)
        with self.assertRaises(ValueError): packet.source_packet({},base=BASE)
        with self.assertRaises(ValueError): packet.source_packet(list(self.files.items()),base=BASE)
        with self.assertRaises(ValueError): packet.source_packet({**self.files,42:b'synthetic'},base=BASE)

    def test_base_requires_canonical_full_commit_shape_without_claiming_provenance(self):
        for base in ('',None,'short','A'*40,'a'*39,'a'*41,'g'*40):
            with self.subTest(base=base):
                with self.assertRaises(ValueError): packet.source_packet(self.files,base=base)
        result=packet.source_packet(self.files,base=BASE)
        self.assertEqual(BASE,result['base']); self.assertFalse(result['execution_authorized'])

    def test_build_literal_validation_without_executing_source(self):
        for raw in (b'APP_BUILD = 78\n',b'APP_BUILD = "not-a-build"\n',
                    b'APP_BUILD = get_build()\n',b'# missing build\n'):
            files=self.files.copy(); files['backend/version.py']=raw
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError): packet.source_packet(files,base=BASE)

    def test_old_build_is_explicitly_not_release_ready(self):
        for build in ('20260916.74','20260928.78'):
            self.files['backend/version.py']=('APP_BUILD = '+repr(build)+'\n').encode()
            result=packet.source_packet(self.files,base=BASE)
            self.assertEqual(build,result['build'])
            self.assertEqual('20260929_0021',result['schema'])
            self.assertEqual('local_source_checked_not_release_ready',result['phase'])
            self.assertFalse(result['execution_authorized'])
            self.assertIn('immutable_release_commit_and_distinct_build',result['pending'])

    def test_receipt_like_source_cannot_override_fixed_pending_or_authority(self):
        files={**self.files,'artifacts/synthetic-approval.json':json.dumps({
            'execution_authorized':True,'pending':[],'phase':'deployed',
            'provider_acceptance':True,'database_acceptance':True}).encode()}
        result=packet.source_packet(files,base=BASE)
        self.assertIs(result['execution_authorized'],False)
        self.assertEqual(list(packet.PENDING),result['pending'])
        self.assertEqual(list(packet.TESTS),result['required_tests'])
        self.assertEqual('local_source_checked_not_release_ready',result['phase'])
        self.assertEqual('compatible_forward_correction_only_no_schema_downgrade',result['rollback'])
        with self.assertRaises(TypeError): packet.source_packet(files,base=BASE,execution_authorized=True)

    def test_returned_lists_do_not_mutate_later_gate_results(self):
        result=packet.source_packet(self.files,base=BASE)
        result['pending'].clear(); result['required_tests'].clear(); result['execution_authorized']=True
        next_result=packet.source_packet(self.files,base=BASE)
        self.assertEqual(list(packet.PENDING),next_result['pending'])
        self.assertEqual(list(packet.TESTS),next_result['required_tests'])
        self.assertIs(next_result['execution_authorized'],False)


if __name__=='__main__': unittest.main()
