import unittest
from unittest.mock import MagicMock, patch

import admission_healthchecks as delivery


class HealthchecksTests(unittest.TestCase):
    url = 'https://hc-ping.com/00000000-0000-0000-0000-000000000001'

    def connection(self, status=200, body=b'OK'):
        conn = MagicMock()
        conn.getresponse.return_value.status = status
        conn.getresponse.return_value.read.return_value = body
        return conn

    def test_success_and_failure_paths(self):
        for classification, suffix in (((),''), (('last_run_failed',),'/fail'),
            (('observation_unavailable',),'/fail')):
            conn = self.connection()
            with patch.object(delivery.http.client, 'HTTPSConnection', return_value=conn) as create:
                self.assertEqual('accepted', delivery.submit(self.url, classification))
            self.assertEqual('hc-ping.com', create.call_args.args[0])
            self.assertEqual(8, create.call_args.kwargs['timeout'])
            self.assertEqual(('POST','/00000000-0000-0000-0000-000000000001'+suffix), conn.request.call_args.args)
            self.assertNotIn(self.url.encode(), conn.request.call_args.kwargs['body'])
            conn.getresponse.return_value.read.assert_called_once_with(3)
            conn.close.assert_called_once()

    def test_misleading_200_and_redirects_are_unconfirmed(self):
        for status, body in ((200,b'OK (not found)'),(200,b'OK (rate limited)'),
            (200,b''),(200,b'OK\n'),(302,b'OK'),(429,b'OK'),(500,b'OK')):
            conn = self.connection(status,body)
            with patch.object(delivery.http.client,'HTTPSConnection',return_value=conn):
                self.assertEqual('unconfirmed', delivery.submit(self.url, ()))
            self.assertEqual(1, conn.request.call_count)

    def test_version2_fixed_lanes_and_unknown_codes(self):
        for classification in ((), ('totp:expired_ciphertext_remaining',), ('admission:last_run_failed',)):
            conn=self.connection()
            with patch.object(delivery.http.client,'HTTPSConnection',return_value=conn):
                self.assertEqual('accepted',delivery.submit(self.url,classification,version=2))
            self.assertIn(b'authenticator',conn.request.call_args.kwargs['body'])
            self.assertEqual(bool(classification),conn.request.call_args.args[1].endswith('/fail'))
            self.assertEqual(1,conn.request.call_count)
        with patch.object(delivery.http.client,'HTTPSConnection') as create:
            self.assertEqual('unconfirmed',delivery.submit(self.url,('totp:raw-private',),version=2))
            create.assert_not_called()

    def test_capability_url_validation_has_no_network_side_effect(self):
        for url in (None,self.url+'/fail',self.url+'?create=1',self.url+'#x',
            self.url.replace('https:','http:'),self.url.replace('hc-ping.com','attacker.invalid'),
            self.url.replace('hc-ping.com','hc-ping.com:443'),self.url+'\n'):
            with patch.object(delivery.http.client,'HTTPSConnection') as create:
                self.assertEqual('unconfirmed', delivery.submit(url, ()))
                create.assert_not_called()

    def test_invalid_classification_cannot_send(self):
        with patch.object(delivery.http.client,'HTTPSConnection') as create:
            self.assertEqual('unconfirmed', delivery.submit(self.url, ('raw-secret',)))
            create.assert_not_called()

    def test_ambiguous_send_does_not_retry_or_report_acceptance(self):
        conn = self.connection()
        conn.getresponse.side_effect = TimeoutError(self.url)
        with patch.object(delivery.http.client,'HTTPSConnection',return_value=conn):
            self.assertEqual('unconfirmed', delivery.submit(self.url, ()))
        self.assertEqual(1, conn.request.call_count)
        conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
