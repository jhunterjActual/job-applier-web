"""Provider-free conditional Graph client contract tests."""
import json
import unittest
from unittest.mock import patch
import os
from deploy.onedrive_retention import graph_delete as g

SHA = 'a'*64


class Fake:
    simulation = True
    def __init__(self, status=204, metadata=None, error=None):
        self.calls = []; self.status = status; self.error = error
        self.metadata = metadata if metadata is not None else {
            'id':'item/one', 'name':'repository.tar', 'size':123, 'file':{},
            'parentReference':{'driveId':'drive/one'}, 'eTag':'"version-1"'}
    def request(self, method, url, headers, grant):
        self.calls.append((method, url, dict(headers)))
        if method == 'GET': return 200, json.dumps(self.metadata).encode()
        if self.error: raise self.error
        return self.status, b''


class Tests(unittest.TestCase):
    def client(self, fake):
        return g.Client(g.offline_grant('drive/one','root/one',SHA,'"version-1"'), fake)

    def test_conditional_same_id_and_fixed_root(self):
        fake=Fake(); self.assertEqual(self.client(fake).soft_delete(SHA,'item/one',123),'soft_delete_accepted')
        self.assertEqual(len(fake.calls),2)
        self.assertEqual(fake.calls[0][1],g.ORIGIN+'/drives/drive%2Fone/items/root%2Fone:/'+SHA+'/repository.tar')
        self.assertEqual(fake.calls[1][1],g.ORIGIN+'/drives/drive%2Fone/items/item%2Fone')
        self.assertEqual(fake.calls[1][2]['If-Match'],'"version-1"')
        self.assertNotIn('Prefer',fake.calls[1][2])

    def test_missing_grant_and_native_unavailable(self):
        for grant in (None,g.Grant('drive','root','secret',SHA,'"version-1"')):
            with self.assertRaises(g.Refused):g.Client(grant)
        with self.assertRaises(g.Refused):g.native_grant()
        with self.assertRaises(g.Refused):g.Client(g.offline_grant('d','r',SHA,'"version-1"'))

    def test_each_metadata_mismatch_prevents_delete(self):
        mutations = [('id','wrong'),('name','other'),('size',124),('size',True),
            ('parentReference',{'driveId':'other'}),('eTag',''),('eTag','bad\r\nheader'),
            ('folder',{}),('remoteItem',{})]
        for key,value in mutations:
            with self.subTest(key=key,value=value):
                fake=Fake();fake.metadata[key]=value
                with self.assertRaises(g.Refused):self.client(fake).soft_delete(SHA,'item/one',123)
                self.assertEqual(len(fake.calls),1)

    def test_missing_etag(self):
        fake=Fake();del fake.metadata['eTag']
        with self.assertRaises(g.Refused):self.client(fake).soft_delete(SHA,'item/one',123)
        self.assertEqual(len(fake.calls),1)

    def test_changed_and_access_refused_no_retry(self):
        fake=Fake(412)
        self.assertEqual(self.client(fake).soft_delete(SHA,'item/one',123),'changed_not_deleted')
        for status in (401,403):
            fake=Fake(status)
            with self.assertRaises(g.Refused):self.client(fake).soft_delete(SHA,'item/one',123)
            self.assertEqual(len(fake.calls),2)

    def test_uncertain_redirect_and_timeout_no_retry(self):
        for status in (301,302,307,308,429,500,200):
            fake=Fake(status)
            with self.assertRaises(g.UnknownOutcome):self.client(fake).soft_delete(SHA,'item/one',123)
            self.assertEqual(len(fake.calls),2)
        fake=Fake(error=TimeoutError('sensitive raw URL'))
        with self.assertRaises(g.UnknownOutcome) as caught:self.client(fake).soft_delete(SHA,'item/one',123)
        self.assertNotIn('sensitive',str(caught.exception));self.assertEqual(len(fake.calls),2)
        self.assertIsNone(g.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_invalid_inputs_no_http(self):
        for sha in ('../other','A'*64,SHA+'/repository.tar'):
            fake=Fake()
            with self.assertRaises(g.Refused):self.client(fake).soft_delete(sha,'item',123)
            self.assertEqual(fake.calls,[])

    def test_secret_representations(self):
        grant=g.offline_grant('d','r',SHA,'"version-1"','VERYSECRET')
        self.assertNotIn('VERYSECRET',repr(grant));self.assertNotIn('VERYSECRET',repr(g.Client(grant,Fake())))

    def test_real_transport_rejects_offline_before_network(self):
        with self.assertRaises(g.Refused):
            g.HttpTransport().request('DELETE',g.ORIGIN+'/drives/d/items/i',{},g.offline_grant('d','r',SHA,'"version-1"'))

    def test_version_drift_prevents_delete(self):
        fake=Fake();fake.metadata['eTag']='"version-2"'
        self.assertEqual(self.client(fake).soft_delete(SHA,'item/one',123),'changed_not_deleted')
        self.assertEqual(len(fake.calls),1)

    def test_grant_tag_and_archive_binding(self):
        for tag in ('*',' * ','','bad\r\nheader','bad\x00header',' leading'):
            fake=Fake()
            with self.assertRaises(g.Refused):g.Client(g.offline_grant('d','r',SHA,tag),fake)
            self.assertEqual(fake.calls,[])
        fake=Fake()
        with self.assertRaises(g.Refused):self.client(fake).soft_delete('b'*64,'item/one',123)
        self.assertEqual(fake.calls,[])
        # eTags are provider-opaque; do not invent a quoted-only grammar.
        fake=Fake();fake.metadata['eTag']='opaque-version'
        client=g.Client(g.offline_grant('drive/one','root/one',SHA,'opaque-version'),fake)
        self.assertEqual(client.soft_delete(SHA,'item/one',123),'soft_delete_accepted')

    def test_readonly_grant_cannot_delete_even_with_concrete_transport(self):
        grant = g.Grant('drive/one','root/one','secret',SHA,'read-only',seal=g._SEAL,
                        offline=True,read_only=True)
        fake = Fake(); client = g.Client(grant,fake)
        self.assertEqual(client.metadata('item/one',123),
            {'object_id':'item/one','size':123,'etag':'"version-1"'})
        with self.assertRaises(g.Refused): client.soft_delete(SHA,'item/one',123)
        self.assertEqual(len(fake.calls),1)
        native = g.Grant('d','r','secret',SHA,'read-only',seal=g._SEAL,read_only=True)
        with patch.object(g.urllib.request,'build_opener') as opener:
            with self.assertRaises(g.Refused):
                g.HttpTransport().request('DELETE',g.ORIGIN+'/drives/d/items/i',{},native)
            opener.assert_not_called()

    def test_native_factory_rejects_invalid_scope_before_config_or_network(self):
        with patch.object(g.urllib.request,'build_opener') as opener:
            for tx,archive in ((None,None),('../escape',SHA),('a'*32,'bad')):
                with self.assertRaises(g.Refused):g.native_grant(tx,archive,1)
            opener.assert_not_called()

    def test_expired_existing_oauth_token_refuses_before_graph_provider_call(self):
        from deploy.onedrive_retention import catalog,capture_plan,entry,hydrate
        token=json.dumps({'access_token':'synthetic-secret','token_type':'Bearer',
                          'expiry':'2000-01-01T00:00:00Z'})
        raw=('[r2]\ntype = s3\n[onedrive]\ntype = onedrive\ndrive_id = synthetic-drive\ntoken = '+token+'\n').encode()
        with patch.object(catalog,'NativeFiles'),patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry,'_native_dependencies'),patch.object(entry,'identity_reverification_idle',return_value=True), \
             patch.object(hydrate,'protected',return_value=(raw,None)),patch.object(g.HttpTransport,'request') as request:
            with self.assertRaisesRegex(g.Refused,'native_graph_credentials_or_mapping_refused'):
                g.native_grant('a'*32,SHA,7,verification=1)
            request.assert_not_called()

    def test_private_refresh_token_rotation_preserves_every_identity_and_root_option(self):
        import configparser,io,datetime as dt
        old={'type':'onedrive','drive_id':'same-drive','root_folder_id':'same-root',
             'client_id':'existing-synthetic-client','token':'expired-synthetic'}
        new=dict(old,token=json.dumps({'access_token':'fresh-synthetic','token_type':'Bearer',
                                      'expiry':'2026-10-27T15:00:00Z'}))
        def encode(value):
            parser=configparser.ConfigParser(interpolation=None);parser['onedrive']=value
            output=io.StringIO();parser.write(output);return output.getvalue().encode()
        now=dt.datetime(2026,10,27,14,tzinfo=dt.timezone.utc)
        self.assertEqual(g.refreshed_remote(old,encode(new),now),new)
        for key,value in (('drive_id','changed'),('root_folder_id','changed'),
                          ('client_id','new-identity'),('type','s3')):
            changed=dict(new,**{key:value})
            with self.assertRaises(g.Refused):g.refreshed_remote(old,encode(changed),now)
        with self.assertRaises(g.Refused):g.refreshed_remote(old,encode(new)+b'\n[r2]\ntype=s3\n',now)
        with self.assertRaises(g.Refused):g.refreshed_remote(old,encode(new),now+dt.timedelta(hours=2))

    def test_factory_uses_existing_private_copy_refresh_without_replacing_root_config(self):
        from deploy.onedrive_retention import catalog,entry,hydrate
        token=json.dumps({'access_token':'expired-synthetic','token_type':'Bearer','expiry':'2000-01-01T00:00:00Z'})
        raw=('[r2]\ntype=s3\n[onedrive]\ntype=onedrive\ndrive_id=same-drive\nclient_id=existing-client\ntoken='+token+'\n').encode()
        fresh={'type':'onedrive','drive_id':'same-drive','client_id':'existing-client',
            'token':json.dumps({'access_token':'fresh-synthetic','token_type':'Bearer','expiry':'2999-01-01T00:00:00Z'})}
        mapped=json.dumps({'id':'mapped-root','name':'postgresql-bundles','folder':{},
                           'parentReference':{'driveId':'same-drive'}}).encode()
        with patch.object(catalog,'NativeFiles'),patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry,'_native_dependencies'),patch.object(entry,'identity_reverification_idle',return_value=True), \
             patch.object(hydrate,'protected',return_value=(raw,('original-identity',))), \
             patch.object(g,'refresh_existing_remote',return_value=fresh) as refresh, \
             patch.object(g.HttpTransport,'request',return_value=(200,mapped)):
            grant=g.native_grant('a'*32,SHA,7,verification=1)
            self.assertTrue(grant.read_only);self.assertEqual(grant.drive_id,'same-drive')
            self.assertEqual(grant.token,'fresh-synthetic');refresh.assert_called_once()
            self.assertEqual(refresh.call_args.args[3],(raw,('original-identity',)))

    @unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,
                         'isolated POSIX root fixture required; never provider acceptance')
    def test_private_copy_actual_child_terminal_and_exact_cleanup(self):
        import configparser,io,tempfile,subprocess,sys
        from pathlib import Path
        from deploy.onedrive_retention import catalog,capture_plan,hydrate
        original={'type':'onedrive','drive_id':'synthetic-drive','token':'expired-synthetic'}
        original_snapshot=(b'synthetic protected original',('original-inode',))
        original_protected=hydrate.protected;spawn=subprocess.Popen
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary:
            root=Path(temporary);source=root/'source';source.mkdir(mode=0o700)
            tx='a'*32;(source/tx).mkdir(mode=0o700)
            private=root/'private-copy';private.mkdir(mode=0o700)
            keep_backup=False
            lock=root/'synthetic.lock';lock.write_bytes(b'');fd=os.open(lock,os.O_RDWR)
            def protected(path,*args,**kwargs):
                if str(path)==capture_plan.CONFIG:return original_snapshot
                if str(path)==capture_plan.BINARY:return b'fixed-binary',('binary-inode',)
                return original_protected(path,*args,**kwargs)
            def launch(args,**kwargs):
                self.assertEqual(args[:3],[capture_plan.BINARY,'about','onedrive:'])
                config=args[args.index('--config')+1]
                self.assertEqual(config,str(private/'rclone.conf'))
                parser=configparser.ConfigParser(interpolation=None);parser.read(config)
                self.assertEqual(parser.sections(),['onedrive'])
                script=("import configparser,json,pathlib,sys,time; "
                    "p=configparser.ConfigParser(interpolation=None); p.read(sys.argv[1]); "
                    "p['onedrive']['token']=json.dumps({'access_token':'fresh-synthetic','token_type':'Bearer','expiry':'2999-01-01T00:00:00Z'}); "
                    "f=open(sys.argv[1],'w'); p.write(f); f.close(); "
                    +("pathlib.Path(sys.argv[1]+'.old-synthetic').write_text('synthetic-only'); " if keep_backup else '')+
                    "print(json.dumps({'total':100,'free':50})); time.sleep(.05)")
                return spawn([sys.executable,'-c',script,config],**kwargs)
            try:
                with patch.object(catalog,'SOURCE_ROOT',source),patch.object(catalog,'NativeFiles'), \
                     patch.object(hydrate,'protected',side_effect=protected), \
                     patch.object(capture_plan,'BINARY_SHA256',g.hashlib_sha(b'fixed-binary')), \
                     patch.object(g.tempfile,'mkdtemp',return_value=str(private)), \
                     patch.object(g.subprocess,'Popen',side_effect=launch):
                    fresh=g.refresh_existing_remote(original,tx,fd,original_snapshot)
                    self.assertEqual(fresh['drive_id'],original['drive_id'])
                    self.assertEqual(g.bearer(fresh,g.dt.datetime.now(g.dt.timezone.utc)),'fresh-synthetic')
                    self.assertFalse(private.exists())
                    names={path.name for path in (source/tx).iterdir()}
                    self.assertEqual(len(names),4)
                    self.assertTrue(any(name.endswith('-cleanup.json') for name in names))
                private=root/'private-copy2';private.mkdir(mode=0o700);keep_backup=True
                with patch.object(catalog,'SOURCE_ROOT',source),patch.object(catalog,'NativeFiles'), \
                     patch.object(hydrate,'protected',side_effect=protected), \
                     patch.object(capture_plan,'BINARY_SHA256',g.hashlib_sha(b'fixed-binary')), \
                     patch.object(g.tempfile,'mkdtemp',return_value=str(private)), \
                     patch.object(g.subprocess,'Popen',side_effect=launch):
                    with self.assertRaises(g.Refused):g.refresh_existing_remote(original,tx,fd,original_snapshot)
                    self.assertEqual({path.name for path in private.iterdir()},
                                     {'rclone.conf','rclone.conf.old-synthetic'})
                    self.assertFalse((source/tx/'oauth-refresh-copy2-cleanup.json').exists())
            finally:os.close(fd)

    def test_metadata_byte_witness_rejects_drift_and_duplicate_fields(self):
        fake=Fake();fake.metadata['size']=124
        with self.assertRaises(g.Refused): self.client(fake).metadata('item/one',123)
        class Duplicate(Fake):
            def request(self,*args): return 200,b'{"id":"first","id":"second"}'
        with self.assertRaises(g.Refused):self.client(Duplicate()).metadata('item/one',123)


if __name__ == '__main__':unittest.main()
