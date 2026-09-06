"""Synthetic account-choice boundary tests; no provider calls or user database."""
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.routing import Route

import app
import local_users
import provider_secrets
import test_p0_regressions as regressions
import web_auth


class AccountChoiceTests(unittest.TestCase):
    def setUp(self):
        environment = regressions.WebAuthenticationTests().staging_environment()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, environment).start()
        self.settings = web_auth.get_auth_configuration()
        self.client = TestClient(app.app, base_url="https://staging-app.jhunterj.com")
        self.addCleanup(self.client.close)
        self.client.headers.update({
            app.TRUSTED_PROXY_TOKEN_HEADER: environment["CAREERTRELLIS_TRUSTED_PROXY_TOKEN"],
            "Origin": "https://staging-app.jhunterj.com",
        })
        self.authenticator = patch.object(app, "authenticate_session").start()
        self.authenticator.return_value = web_auth.SessionAuthentication(
            status="authenticated", workos_user_id="user_01SYNTHETICONE",
            session_id="session-synthetic-one", refreshed_session="refreshed-must-not-return",
        )
        self.resolver = patch.object(app, "authorized_local_user").start()
        self.resolver.return_value = local_users.AuthorizedLocalUser(1, "owner")
        self.logout_builder = patch.object(app, "logout_url").start()
        self.logout_builder.return_value = "https://authkit.test/logout"
        self.client.cookies.set(web_auth.SESSION_COOKIE_NAME, "synthetic-old-session",
                                domain="staging-app.jhunterj.com", path="/")
        self.client.cookies.set(web_auth.LOGIN_FLOW_COOKIE_NAME, "synthetic-old-flow",
                                domain="staging-app.jhunterj.com", path="/")

    def assert_cookies_cleared(self, response):
        cookies = response.headers.get_list("set-cookie")
        for name in (web_auth.SESSION_COOKIE_NAME, web_auth.LOGIN_FLOW_COOKIE_NAME):
            matching = [value for value in cookies if value.startswith(name + "=")]
            self.assertEqual(1, len(matching))
            for attribute in ("Max-Age=0", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
                self.assertIn(attribute, matching[0])
            self.assertIsNone(self.client.cookies.get(name))
        self.assertNotIn("refreshed-must-not-return", str(cookies))
        self.assertEqual("no-store, max-age=0", response.headers["cache-control"])
        self.assertEqual("no-referrer", response.headers["referrer-policy"])

    def test_logout_clears_both_cookies_without_refresh_or_owner_lookup(self):
        response = self.client.post("/auth/logout", follow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual("https://authkit.test/logout", response.headers["location"])
        self.assert_cookies_cleared(response)
        self.logout_builder.assert_called_once_with(self.settings, "session-synthetic-one")
        self.resolver.assert_not_called()

    def test_logout_builder_failure_is_explicit_and_does_not_reauthenticate(self):
        self.logout_builder.side_effect = RuntimeError("private-provider-detail")
        response = self.client.post("/auth/logout", follow_redirects=False)
        self.assertEqual(503, response.status_code)
        self.assertIn("could not confirm", response.text)
        self.assertIn('href="/auth/sign-in"', response.text)
        self.assertNotIn("private-provider-detail", response.text)
        self.assertNotIn("location", response.headers)
        self.assert_cookies_cleared(response)

    def test_unverifiable_sessions_still_allow_local_sign_out_only(self):
        for result in (
            web_auth.SessionAuthentication(status="unavailable"),
            web_auth.SessionAuthentication(status="unauthenticated"),
            web_auth.SessionAuthentication(status="authenticated", workos_user_id="user_01SYNTHETICONE"),
        ):
            with self.subTest(status=result.status):
                self.authenticator.return_value = result
                response = self.client.post("/auth/logout", follow_redirects=False)
                self.assertEqual(503, response.status_code)
                self.assert_cookies_cleared(response)
        self.logout_builder.assert_not_called()
        self.resolver.assert_not_called()

    def test_authentication_exception_and_oversized_cookie_are_private_local_logout(self):
        self.authenticator.side_effect = RuntimeError("private-jwks-detail")
        response = self.client.post("/auth/logout", follow_redirects=False)
        self.assertEqual(503, response.status_code)
        self.assertNotIn("private-jwks-detail", response.text)
        self.assert_cookies_cleared(response)
        self.authenticator.reset_mock()
        response = self.client.post("/auth/logout", headers={
            "Cookie": web_auth.SESSION_COOKIE_NAME + "=" + "x" * 8193,
        }, follow_redirects=False)
        self.assertEqual(503, response.status_code)
        self.authenticator.assert_not_called()
        self.assert_cookies_cleared(response)

    def test_cross_site_logout_is_rejected_without_touching_cookies_or_provider(self):
        response = self.client.post("/auth/logout", headers={
            "Origin": "https://attacker.test", "Sec-Fetch-Site": "cross-site",
        }, follow_redirects=False)
        self.assertEqual(403, response.status_code)
        self.assertNotIn("set-cookie", response.headers)
        self.authenticator.assert_not_called()
        self.logout_builder.assert_not_called()

    def test_invalid_workos_configuration_still_clears_local_logout_state(self):
        with patch.object(app, "get_auth_configuration", side_effect=web_auth.AuthenticationConfigurationError("private-detail")):
            response = self.client.post("/auth/logout", follow_redirects=False)
        self.assertEqual(503, response.status_code)
        self.assertNotIn("private-detail", response.text)
        self.assert_cookies_cleared(response)
        self.authenticator.assert_not_called()
        self.logout_builder.assert_not_called()

    def test_root_return_stops_at_public_landing_and_stale_callback_cannot_complete(self):
        self.client.post("/auth/logout", follow_redirects=False)
        self.authenticator.side_effect = lambda settings, cookie: web_auth.SessionAuthentication(
            status="unauthenticated",
        )
        with patch.object(app, "begin_login") as begin, patch.object(app, "complete_login") as complete:
            response = self.client.get("/", follow_redirects=True)
            self.assertEqual("/auth/sign-in", response.url.path)
            self.assertEqual(200, response.status_code)
            self.assertEqual("no-store, max-age=0", response.headers["cache-control"])
            self.assertIn("Use a different account", response.text)
            stale = self.client.get("/auth/callback?code=synthetic-code&state=old-state")
            self.assertEqual(400, stale.status_code)
            begin.assert_not_called()
            complete.assert_not_called()

    def test_explicit_login_choice_uses_fixed_options_and_clears_previous_session(self):
        pending = web_auth.PendingLogin("https://authkit.test/authorize", "new-synthetic-flow")
        for query, different in (("", False), ("?account=different&prompt=none&login_hint=untrusted", True)):
            with self.subTest(different=different), patch.object(app, "begin_login", return_value=pending) as begin:
                response = self.client.get("/auth/login" + query, follow_redirects=False)
                self.assertEqual(303, response.status_code)
                begin.assert_called_once_with(self.settings, different_account=different)
                self.assertIsNone(self.client.cookies.get(web_auth.SESSION_COOKIE_NAME))
                self.assertEqual("new-synthetic-flow", self.client.cookies.get(web_auth.LOGIN_FLOW_COOKIE_NAME))
        with patch.object(app, "begin_login") as begin:
            for query in ("?account=other", "?account=different&account=different"):
                self.assertEqual(400, self.client.get("/auth/login" + query).status_code)
            begin.assert_not_called()

    def test_login_provider_failure_clears_old_flow_and_session(self):
        with patch.object(app, "begin_login", side_effect=RuntimeError("private-detail")):
            response = self.client.get("/auth/login?account=different", follow_redirects=False)
        self.assertEqual(503, response.status_code)
        self.assertEqual({"detail": "Authentication is temporarily unavailable."}, response.json())
        self.assert_cookies_cleared(response)

    def test_real_sdk_builds_pkce_authkit_url_with_upstream_choice_without_network(self):
        # URL generation is local SDK work, not a live provider acceptance test.
        for different in (False, True):
            with self.subTest(different=different), patch("httpx.Client.send", side_effect=AssertionError("Network forbidden")):
                pending = web_auth.begin_login(self.settings, different_account=different)
                query = parse_qs(urlsplit(pending.authorization_url).query)
            self.assertEqual(["authkit"], query["provider"])
            self.assertEqual(["login"], query["prompt"])
            self.assertEqual(["sign-in"], query["screen_hint"])
            self.assertEqual(["S256"], query["code_challenge_method"])
            self.assertTrue(query["code_challenge"][0])
            self.assertNotIn("login_hint", query)
            self.assertNotIn("code_verifier", query)
            if different:
                self.assertEqual(["select_account"], query["provider_query_params[prompt]"])
            else:
                self.assertNotIn("provider_query_params[prompt]", query)
            self.assertNotIn("provider_query_params", query)
            state, verifier = web_auth._open_login_flow(self.settings, pending.sealed_flow)
            self.assertEqual(query["state"][0], state)
            self.assertTrue(verifier)

    def test_two_sequential_users_resolve_independent_owners_and_unlisted_is_denied(self):
        async def owner_probe(request):
            return JSONResponse({"owner": app.current_owner_user_id()})

        route = Route("/api/synthetic-owner-probe", owner_probe)
        app.app.router.routes.append(route)
        self.addCleanup(app.app.router.routes.remove, route)
        identities = {"one": ("user_01SYNTHETICONE", 1), "two": ("user_01SYNTHETICTWO", 2)}
        self.authenticator.side_effect = lambda settings, cookie: (
            web_auth.SessionAuthentication(status="authenticated", workos_user_id=identities[cookie][0], session_id="synthetic-session")
            if cookie in identities else web_auth.SessionAuthentication(status="unauthenticated")
        )
        self.resolver.side_effect = lambda identity, owner, **kwargs: next((
            local_users.AuthorizedLocalUser(number, "owner" if number == 1 else "user")
            for candidate, number in identities.values() if candidate == identity
        ), None)
        for cookie, (_, number) in identities.items():
            self.client.cookies.set(web_auth.SESSION_COOKIE_NAME, cookie, domain="staging-app.jhunterj.com", path="/")
            response = self.client.get("/api/synthetic-owner-probe")
            self.assertEqual(200, response.status_code)
            self.assertEqual({"owner": number}, response.json())
            self.assertEqual(identities[cookie][0], self.resolver.call_args.args[0])
            with self.assertRaises(provider_secrets.ProviderSecretAuthorizationError):
                app.current_owner_user_id()
            self.client.post("/auth/logout", follow_redirects=False)
            self.assertEqual(401, self.client.get("/api/session").status_code)
            with self.assertRaises(provider_secrets.ProviderSecretAuthorizationError):
                app.current_owner_user_id()
        self.authenticator.side_effect = None
        self.authenticator.return_value = web_auth.SessionAuthentication(status="authenticated", workos_user_id="user_01UNLISTED")
        self.assertEqual(401, self.client.get("/api/session").status_code)
        self.resolver.return_value = None
        self.resolver.side_effect = None
        self.authenticator.return_value = web_auth.SessionAuthentication(status="authenticated", workos_user_id="user_01SYNTHETICONE")
        self.assertEqual(401, self.client.get("/api/session").status_code)


if __name__ == "__main__":
    unittest.main()
