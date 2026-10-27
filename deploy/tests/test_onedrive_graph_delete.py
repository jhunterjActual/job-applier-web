"""Provider-free conditional Graph client contract tests."""
import json
import unittest
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


if __name__ == '__main__':unittest.main()
