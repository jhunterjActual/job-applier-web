"""Actual application HTTP boundary, synthetic signed sessions and provider only."""
from dataclasses import replace
import asyncio
import html
import os
from pathlib import Path
import re
import tempfile
import time
import unittest

from cryptography.fernet import Fernet

import signup_http
import test_session_epoch_http as epoch_http
import test_signup_http as signup_tests
from test_provider_session_candidate import VerifiedTokens
from test_totp_enrollment import DurableFixture, ProviderFixture, SECRET, FACTOR, CHALLENGE
from totp_enrollment import EnrollmentCoordinator, EnrollmentSetup
from totp_http import EnrollmentHTTP, BASE


class EnrollmentHTTPTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(signup_tests.SignupHTTPTests.setUpClass.__func__)

    async def asyncSetUp(self):
        await epoch_http.SessionEpochHTTPTests.asyncSetUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.enroll_store = DurableFixture(Path(self.directory.name) / 'enrollment.sqlite')
        self.addCleanup(self.enroll_store.db.close)
        self.factor_provider = ProviderFixture()
        async def enroll(subject):
            self.factor_provider.creates += 1
            return EnrollmentSetup(FACTOR, CHALLENGE, SECRET, int(time.time()) + 240)
        self.factor_provider.enroll = enroll
        self.coordinator = EnrollmentCoordinator(self.enroll_store, self.factor_provider, Fernet.generate_key())
        self.runtime.enrollment_http = EnrollmentHTTP(self.runtime, self.coordinator)
        self.fresh_cookies = []
        for cookie in self.cookies:
            session = self.codec.open(cookie)
            identity = VerifiedTokens(session.subject, session.session_id, session.generation,
                self.http.token(sub=session.subject, auth_time=int(time.time())-10), 'synthetic-refresh',
                int(time.time())+300,session.provider_binding)
            self.fresh_cookies.append(self.codec.pack(replace(session,provider=self.codec.provider_seal(identity))))

    def headers(self, index=0, *, fresh=True):
        values = self.fresh_cookies if fresh else self.cookies
        return {'Cookie': signup_http.COOKIE_NAME+'='+values[index]+'; '+signup_http.BROWSER+'='+'b'*43}

    def fields(self, response):
        return {name:html.unescape(re.search('name="'+name+'" value="([^"]+)"',response.text)[1])
                for name in ('csrf','action')}

    async def pending(self):
        response = await self.client.get(BASE, headers=self.headers())
        self.assertEqual(200,response.status_code)
        response = await self.client.post(BASE+'/start', headers=self.headers(), data=self.fields(response))
        self.assertEqual(200,response.status_code)
        return response

    async def test_session_capability_tracks_enabled_handler(self):
        response = await self.client.get('/api/session', headers=self.headers())
        self.assertIs(response.json()['authenticator_setup_available'], True)
        self.runtime.enrollment_http = None
        response = await self.client.get('/api/session', headers=self.headers())
        self.assertIs(response.json()['authenticator_setup_available'], False)

    async def test_dashboard_invitation_keyboard_dismissal_preserves_unsaved_input(self):
        from playwright.async_api import async_playwright
        import json
        root = Path(__file__).parent/'static'
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge' if os.name=='nt' else None, headless=True,
                args=['--host-resolver-rules=MAP * ~NOTFOUND'])
            try:
                page = await browser.new_page(viewport={'width':375,'height':760}, service_workers='block')
                capability = False
                async def route(request):
                    from urllib.parse import urlsplit
                    path = urlsplit(request.request.url).path
                    if path == '/':
                        await request.fulfill(content_type='text/html', body=(root/'index.html').read_bytes())
                    elif path.startswith('/static/') and '/' not in path.removeprefix('/static/'):
                        file = root/path.removeprefix('/static/')
                        if file.suffix in ('.js','.css') and file.is_file():
                            await request.fulfill(content_type='text/javascript' if file.suffix=='.js' else 'text/css', body=file.read_bytes())
                        else: await request.abort()
                    elif path == '/api/session':
                        await request.fulfill(content_type='application/json',body=json.dumps({
                            'authentication':'workos','authenticated':True,'authenticator_setup_available':capability}))
                    else:
                        await request.fulfill(content_type='application/json',body='{}')
                await page.route('**/*',route)
                await page.goto('https://synthetic.invalid/')
                await page.evaluate('configureSessionControls()')
                self.assertFalse(await page.locator('#account-security-link').is_visible())
                self.assertTrue(await page.locator('#authenticator-invitation').evaluate('(node)=>node.hidden'))
                capability = True
                await page.evaluate('configureSessionControls()')
                self.assertTrue(await page.locator('#account-security-link').is_visible())
                # Open the actual profile tab before entering unsaved synthetic text.
                await page.locator('#nav-profile').click()
                await page.locator('#p-name').fill('Synthetic unsaved name')
                await page.locator('#dismiss-authenticator-invitation').focus()
                await page.keyboard.press('Enter')
                self.assertEqual('Synthetic unsaved name',await page.locator('#p-name').input_value())
                self.assertFalse(await page.locator('#authenticator-invitation').is_visible())
                self.assertEqual('p-interface-language',await page.evaluate('document.activeElement.id'))
                await page.evaluate('configureSessionControls()')
                self.assertFalse(await page.locator('#authenticator-invitation').is_visible())
                self.assertFalse(await page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            finally:
                await browser.close()

    async def test_real_boundary_success_and_workspace_preserved(self):
        pending = await self.pending()
        self.assertIn(SECRET,pending.text)
        self.assertIn('no-store',pending.headers['cache-control'])
        self.assertEqual('no-referrer',pending.headers['referrer-policy'])
        self.assertIn("frame-ancestors 'none'",pending.headers['content-security-policy'])
        self.assertNotIn(FACTOR,pending.text)
        done = await self.client.post(BASE+'/verify', headers=self.headers(), data={**self.fields(pending),'code':'123456'})
        self.assertEqual(200,done.status_code)
        self.assertIn('enrollment is verified',done.text)
        self.assertNotIn(SECRET,done.text)
        for path in ('/api/version','/'):
            self.assertEqual(200,(await self.client.get(path,headers=self.headers())).status_code)

    async def test_browser_lost_response_clears_secret_and_focuses_recovery_without_retry(self):
        from playwright.async_api import async_playwright
        pending = await self.pending()
        posts=[]
        async with async_playwright() as p:
            browser=await p.chromium.launch(channel='msedge' if os.name=='nt' else None,headless=True,
                args=['--host-resolver-rules=MAP * ~NOTFOUND'])
            try:
                page=await browser.new_page(service_workers='block')
                async def intercept(route):
                    if route.request.method=='POST':
                        posts.append(route.request.url); await route.abort()
                    elif route.request.url.endswith('/static/totp-enrollment.js'):
                        await route.fulfill(content_type='text/javascript',body=(Path(__file__).parent/'static/totp-enrollment.js').read_bytes())
                    elif route.request.url.endswith(BASE):
                        await route.fulfill(content_type='text/html',body=pending.content)
                    else: await route.abort()
                await page.route('**/*',intercept)
                await page.goto('https://synthetic.invalid'+BASE)
                await page.get_by_label('Authenticator code').fill('123456')
                await page.get_by_role('button',name='Verify code',exact=True).focus()
                await page.keyboard.press('Enter')
                await page.get_by_role('link',name='Reopen account security').wait_for()
                self.assertNotIn(SECRET,await page.content())
                self.assertEqual(0,await page.locator('input').count())
                self.assertEqual('status',await page.evaluate('document.activeElement.getAttribute("role")'))
                await page.keyboard.press('Tab')
                self.assertEqual('Reopen account security',await page.evaluate('document.activeElement.textContent'))
                self.assertEqual(1,len(posts))
                self.assertEqual(0,self.factor_provider.verifications)
            finally:
                await browser.close()

    async def test_default_disabled_and_missing_recent_auth_fail_closed(self):
        self.runtime.enrollment_http = None
        self.assertEqual(404,(await self.client.get(BASE,headers=self.headers())).status_code)
        self.runtime.enrollment_http = EnrollmentHTTP(self.runtime,self.coordinator)
        self.assertEqual(403,(await self.client.get(BASE,headers=self.headers(fresh=False))).status_code)
        self.assertEqual(0,self.factor_provider.creates)

    async def test_cross_origin_missing_csrf_and_account_switch_rejected(self):
        ready = await self.client.get(BASE,headers=self.headers())
        fields = self.fields(ready)
        response = await self.client.post(BASE+'/start',headers={**self.headers(),'Origin':'https://wrong.invalid'},data=fields)
        self.assertEqual(403,response.status_code)  # Outer same-origin middleware rejects first.
        self.assertEqual(400,(await self.client.post(BASE+'/start',headers=self.headers(),data={'action':fields['action']})).status_code)
        self.assertEqual(403,(await self.client.post(BASE+'/start',headers=self.headers(1),data=fields)).status_code)
        self.assertEqual(0,self.factor_provider.creates)

    async def test_duplicate_cookie_and_repeated_start_denied(self):
        response = await self.pending()
        duplicate = self.headers()
        duplicate['Cookie']+='; '+signup_http.COOKIE_NAME+'='+self.fresh_cookies[0]
        self.assertEqual(400,(await self.client.get(BASE,headers=duplicate)).status_code)
        # A setup-scoped form cannot create another factor.
        self.assertEqual(403,(await self.client.post(BASE+'/start',headers=self.headers(),data=self.fields(response))).status_code)
        self.assertEqual(1,self.factor_provider.creates)

    async def test_close_does_not_delete_and_unknown_verification_can_be_checked(self):
        response = await self.pending()
        self.factor_provider.lost_verify = True
        unknown = await self.client.post(BASE+'/verify',headers=self.headers(),data={**self.fields(response),'code':'123456'})
        self.assertEqual(503,unknown.status_code)
        revisit = await self.client.get(BASE,headers=self.headers())
        self.assertNotIn(SECRET,revisit.text)
        done = await self.client.post(BASE+'/check',headers=self.headers(),data=self.fields(revisit))
        self.assertIn('enrollment is verified',done.text)
        self.assertEqual(1,self.factor_provider.verifications)

    async def test_revoked_session_blocks_before_factor_create(self):
        ready = await self.client.get(BASE,headers=self.headers())
        session = self.codec.open(self.fresh_cookies[0])
        await self.runtime.coordination.revoke(session)
        response = await self.client.post(BASE+'/start',headers=self.headers(),data=self.fields(ready))
        self.assertEqual(403,response.status_code)
        self.assertEqual(0,self.factor_provider.creates)

    async def test_browser_submits_origin_without_referrer_and_completes(self):
        from playwright.async_api import async_playwright
        from urllib.parse import urlsplit
        origin = str(self.client.base_url).rstrip('/')
        posts = []
        observed = []
        application_loop = asyncio.get_running_loop()
        async def intercept(route):
            request = route.request
            if not request.url.startswith(origin + '/'):
                await route.abort()
                return
            path = urlsplit(request.url).path
            incoming = await request.all_headers()
            if request.method == 'POST':
                posts.append((incoming.get('origin'), incoming.get('referer')))
            # Transport adapter substitutes only the trusted ingress token and
            # synthetic session cookies. Browser Origin and body remain untouched.
            headers = dict(incoming)
            headers['cookie'] = self.headers()['Cookie']
            headers['origin'] = incoming.get('origin','')
            response = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
                self.client.request(request.method,path,headers=headers,content=request.post_data_buffer),
                application_loop))
            observed.append((path,response.status_code,response.headers.get('content-type')))
            outgoing = dict(response.headers)
            outgoing.pop('content-length',None)
            outgoing.pop('content-encoding',None)
            await route.fulfill(status=response.status_code,headers=outgoing,body=response.content)
        async def browser_scenario():
            async with async_playwright() as p:
                browser = await p.chromium.launch(channel='msedge' if os.name=='nt' else None,
                    headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND'])
                try:
                    context = await browser.new_context(service_workers='block',viewport={'width':375,'height':760})
                    await context.route('**/*',intercept)
                    page = await context.new_page()
                    errors = []
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    await page.goto(origin+BASE)
                    await page.wait_for_timeout(300)
                    self.assertFalse(await page.get_by_role('button',name='Add authenticator',exact=True).is_disabled(), (observed,errors))
                    async with page.expect_response(lambda response:response.url.endswith(BASE+'/start')) as started:
                        await page.get_by_role('button',name='Add authenticator',exact=True).focus()
                        await page.keyboard.press('Enter')
                    self.assertEqual(200,(await started.value).status,(observed,errors,posts))
                    await page.wait_for_function('document.activeElement === document.querySelector("h1")')
                    await page.get_by_label('Authenticator code').fill('123456')
                    await page.get_by_role('button',name='Verify code',exact=True).focus()
                    await page.keyboard.press('Enter')
                    await page.get_by_text('Your authenticator enrollment is verified.',exact=True).wait_for()
                    self.assertEqual([(origin,None),(origin,None)],posts)
                    self.assertFalse(await page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    self.assertNotIn(SECRET,await page.content())
                    self.assertEqual('H1',await page.evaluate('document.activeElement.tagName'))
                finally:
                    await browser.close()
        def browser_thread():
            # psycopg needs Selector on Windows; Playwright subprocesses need
            # Proactor. Keep application/database I/O on its owning test loop.
            factory = asyncio.ProactorEventLoop if os.name=='nt' else asyncio.new_event_loop
            with asyncio.Runner(loop_factory=factory) as runner:
                runner.run(browser_scenario())
        await asyncio.to_thread(browser_thread)


if __name__=='__main__':
    unittest.main()
