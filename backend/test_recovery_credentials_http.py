"""Real signed-session HTTP boundary with synthetic transactional credentials."""
import html
import os
from pathlib import Path
import re
import time
import unittest
from uuid import uuid4

from recovery_credentials import RecoveryCredentialCoordinator
from recovery_credentials_http import BASE, RecoveryCredentialsHTTP
from test_factor_removal_http import RemovalProvider, FACTOR, CHALLENGE
from test_recovery_credentials import CredentialStore
import test_totp_http as enrollment_tests
from totp_enrollment import EnrollmentRecord


class RecoveryCredentialsHTTPTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(enrollment_tests.EnrollmentHTTPTests.setUpClass.__func__)
    headers = enrollment_tests.EnrollmentHTTPTests.headers

    async def asyncSetUp(self):
        await enrollment_tests.EnrollmentHTTPTests.asyncSetUp(self)
        self.credential_store = CredentialStore(Path(self.directory.name) / 'recovery.sqlite', time.time)
        self.addCleanup(self.credential_store.db.close)
        async def eligible(actor):
            auth = actor.authorization
            key = self.runtime.coordination.digest('session', auth.local_id, auth.subject, actor.session_id)
            return key not in self.store.revoked and self.credential_store.allowed
        self.credential_store.eligible = eligible
        async def enrolled_factor(actor):
            return FACTOR
        self.credential_store.enrolled_factor = enrolled_factor
        self.provider = RemovalProvider(self.codec.provider_binding)
        self.credentials = RecoveryCredentialCoordinator(self.credential_store, self.provider)
        class Enrolled:
            async def get(_self, actor):
                return EnrollmentRecord(str(uuid4()), actor, 'complete', int(time.time()) + 240,
                                        FACTOR, CHALLENGE, None, 1)
        self.handler = RecoveryCredentialsHTTP(self.runtime, self.credentials, Enrolled())
        self.runtime.recovery_http = self.handler

    def fields(self, response, verb, index=0):
        forms = re.findall(r'<form method="post" action="' + re.escape(BASE + '/' + verb) + r'">(.*?)</form>', response.text, re.S)
        self.assertGreater(len(forms), index, response.text)
        return {name: html.unescape(re.search('name="' + name + '" value="([^"]+)"', forms[index])[1])
                for name in ('csrf', 'action')}

    async def start(self, index=0):
        ready = await self.client.get(BASE, headers=self.headers())
        self.assertEqual(ready.status_code, 200, ready.text)
        response = await self.client.post(BASE + '/start', headers=self.headers(), data=self.fields(ready, 'start', index))
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def display(self):
        challenge = await self.start()
        fields = {**self.fields(challenge, 'verify'), 'code': '123456'}
        response = await self.client.post(BASE + '/verify', headers=self.headers(), data=fields)
        self.assertEqual(response.status_code, 200, response.text)
        codes = html.unescape(re.search(r'autocomplete="off">(.*?)</textarea>', response.text, re.S)[1]).splitlines()
        self.assertEqual(len(codes), 8)
        return response, codes, fields

    async def test_display_custody_activation_and_no_redisplay(self):
        display, codes, verify = await self.display()
        self.assertIn('no-store', display.headers['cache-control'])
        self.assertEqual(display.headers['referrer-policy'], 'no-referrer')
        self.assertNotIn(codes[0], str(self.credential_store.db.execute('SELECT body FROM pending').fetchall()))
        replay = await self.client.post(BASE + '/verify', headers=self.headers(), data=verify)
        self.assertEqual(replay.status_code, 403)
        ready = await self.client.get(BASE, headers=self.headers())
        self.assertNotIn(codes[0], ready.text)
        done = await self.client.post(BASE + '/confirm', headers=self.headers(),
            data={**self.fields(display, 'confirm'), 'code': codes[0]})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertIn('codes are active', done.text)
        self.assertNotIn(codes[0], done.text)
        self.assertEqual(self.credential_store.db.execute('SELECT count(*) FROM active').fetchone()[0], 1)

    async def test_origin_csrf_and_switched_actor_cannot_issue(self):
        ready = await self.client.get(BASE, headers=self.headers())
        fields = self.fields(ready, 'start')
        wrong = await self.client.post(BASE + '/start', headers={**self.headers(), 'Origin': 'https://wrong.invalid'}, data=fields)
        self.assertEqual(wrong.status_code, 403)
        missing = await self.client.post(BASE + '/start', headers=self.headers(), data={'action': fields['action']})
        self.assertEqual(missing.status_code, 400)
        switched = await self.client.post(BASE + '/start', headers=self.headers(1), data=fields)
        self.assertEqual(switched.status_code, 403)
        self.assertEqual(self.provider.effects, [])

    async def test_stale_session_and_absent_owned_factor_refuse(self):
        self.assertEqual((await self.client.get(BASE, headers=self.headers(fresh=False))).status_code, 403)
        self.provider.absent = True
        ready = await self.client.get(BASE, headers=self.headers())
        response = await self.client.post(BASE + '/start', headers=self.headers(), data=self.fields(ready, 'start'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.provider.effects, [])

    async def test_lost_proposal_ack_never_displays_or_retries(self):
        challenge = await self.start()
        self.credential_store.fail = 'propose'
        fields = {**self.fields(challenge, 'verify'), 'code': '123456'}
        uncertain = await self.client.post(BASE + '/verify', headers=self.headers(), data=fields)
        self.assertEqual(uncertain.status_code, 503)
        self.assertNotIn('id="recovery-codes"', uncertain.text)
        self.credential_store.fail = None
        replay = await self.client.post(BASE + '/verify', headers=self.headers(), data=fields)
        self.assertEqual(replay.status_code, 403)
        self.assertEqual(self.provider.effects.count('verify'), 1)

    async def test_wrong_custody_does_not_activate_or_redisplay(self):
        display, codes, _ = await self.display()
        response = await self.client.post(BASE + '/confirm', headers=self.headers(),
            data={**self.fields(display, 'confirm'), 'code': 'x' * 43})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(codes[0], response.text)
        self.assertEqual(self.credential_store.db.execute('SELECT count(*) FROM active').fetchone()[0], 0)

    async def test_replacement_preserves_old_set_until_new_custody_confirmed(self):
        display, codes, _ = await self.display()
        first = await self.client.post(BASE + '/confirm', headers=self.headers(),
            data={**self.fields(display, 'confirm'), 'code': codes[0]})
        self.assertEqual(first.status_code, 200)
        old_generation = self.credential_store.db.execute('SELECT generation FROM active').fetchone()[0]
        challenge = await self.start(index=1)
        replacement = await self.client.post(BASE + '/verify', headers=self.headers(),
            data={**self.fields(challenge, 'verify'), 'code': '123456'})
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(self.credential_store.db.execute('SELECT generation FROM active').fetchone()[0], old_generation)
        new_code = html.unescape(re.search(r'autocomplete="off">(.*?)</textarea>', replacement.text, re.S)[1]).splitlines()[0]
        confirmed = await self.client.post(BASE + '/confirm', headers=self.headers(),
            data={**self.fields(replacement, 'confirm'), 'code': new_code})
        self.assertEqual(confirmed.status_code, 200)
        self.assertNotEqual(self.credential_store.db.execute('SELECT generation FROM active').fetchone()[0], old_generation)
        self.assertEqual(self.credential_store.db.execute("SELECT count(*) FROM pending WHERE state='revoked'").fetchone()[0], 1)

    async def test_wrong_purpose_and_duplicate_start_do_not_dispatch(self):
        ready = await self.client.get(BASE, headers=self.headers())
        wrong = await self.client.post(BASE + '/start', headers=self.headers(), data=self.fields(ready, 'start', 1))
        self.assertEqual(wrong.status_code, 403)
        fields = self.fields(ready, 'start')
        first = await self.client.post(BASE + '/start', headers=self.headers(), data=fields)
        self.assertEqual(first.status_code, 200)
        second = await self.client.post(BASE + '/start', headers=self.headers(), data=fields)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(self.provider.effects.count('challenge'), 1)

    async def test_browser_clears_codes_on_unknown_response_without_retry(self):
        from playwright.async_api import async_playwright
        display, codes, _ = await self.display()
        posts = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel='msedge' if os.name == 'nt' else None,
                headless=True, args=['--host-resolver-rules=MAP * ~NOTFOUND'])
            try:
                page = await browser.new_page(service_workers='block')
                async def route(request):
                    if request.request.method == 'POST':
                        posts.append(request.request.url)
                        await request.abort()
                    elif request.request.url.endswith('/static/recovery-credentials.js'):
                        await request.fulfill(content_type='text/javascript',
                            body=Path(__file__).with_name('static').joinpath('recovery-credentials.js').read_bytes())
                    elif request.request.url.endswith(BASE):
                        await request.fulfill(content_type='text/html', body=display.content)
                    else:
                        await request.abort()
                await page.route('**/*', route)
                await page.goto('https://synthetic.invalid' + BASE)
                self.assertIn(codes[0], await page.locator('#recovery-codes').input_value())
                await page.get_by_label('One saved recovery code').fill(codes[0])
                await page.get_by_role('button', name='Activate my saved recovery codes').click()
                await page.get_by_text('Recovery codes could not be confirmed. Do not repeat this action. Contact support to check its outcome.', exact=True).wait_for()
                self.assertEqual(len(posts), 1)
                self.assertNotIn(codes[0], await page.content())
                self.assertEqual(await page.locator('input, textarea, button').count(), 0)
                self.assertEqual(await page.evaluate('document.activeElement.getAttribute("role")'), 'status')
                self.assertEqual(await page.evaluate('localStorage.length + sessionStorage.length'), 0)
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
