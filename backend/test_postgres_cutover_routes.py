"""Shared-route qualification against disposable PostgreSQL.

Set CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL to an isolated PostgreSQL 18 server.
The integration class reuses the reviewed disposable-role harness, exercises
both synthetic owners, and skips without that explicit test URL.
"""

from __future__ import annotations

import base64
from contextlib import ExitStack
import json
import os
import unittest
from unittest.mock import Mock, patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

import app as app_module
import generated_material_repository as generated_material_module
from local_users import AuthorizedLocalUser
import object_store as object_store_module
import postgres_database as postgres_database_module
import stored_objects as stored_objects_module
import test_postgres_rls as postgres_harness
from provider_secrets import SHARED_SECRET_STORE
from web_auth import SESSION_COOKIE_NAME, SessionAuthentication


# Every API route is assigned to one reviewed shared-host policy. This inventory
# makes a new route fail the qualification suite until its authority boundary is
# explicit; the disposable integration test below exercises each repository
# family without attempting destructive or external-provider mutations. Routes
# whose stable public shape is empty in this fixture are dispatch/no-SQLite
# smoke evidence; owner-distinct assertions below cover the private payloads.
SHARED_ROUTE_POLICIES: dict[str, set[tuple[str, str]]] = {
    "public-maintenance-notice": {("GET", "/api/maintenance-notice")},
    "session-and-build": {
        ("GET", "/api/session"),
        ("GET", "/api/version"),
    },
    "private-objects": {
        ("GET", "/api/private-objects/{object_id}"),
    },
    "profile-and-resumes": {
        ("GET", "/api/profile"),
        ("POST", "/api/profile"),
        ("GET", "/api/base-resumes"),
        ("GET", "/api/base-resumes/{resume_id}"),
        ("POST", "/api/base-resumes/{resume_id}/activate"),
        ("GET", "/api/base-resumes/{resume_id}/versions"),
        ("GET", "/api/base-resumes/{resume_id}/versions/{version_number}"),
        ("POST", "/api/base-resumes/{resume_id}/versions/{version_number}/restore"),
        ("DELETE", "/api/base-resumes/{resume_id}"),
    },
    "provider-context": {
        ("PUT", "/api/profile/secrets"),
        ("POST", "/api/profile/ai-provider/validate"),
        ("POST", "/api/profile/maps-provider/validate"),
        ("POST", "/api/profile/upload-resume"),
    },
    "search-runs": {
        ("GET", "/api/operations/{operation_id}/status"),
        ("POST", "/api/operations/{operation_id}/cancel"),
        ("GET", "/api/jobs/status"),
        ("POST", "/api/jobs/search"),
        ("POST", "/api/jobs/search/plan"),
        ("POST", "/api/jobs/search/cancel"),
    },
    "discovery": {
        ("GET", "/api/jobs"),
        ("POST", "/api/jobs/import/preview"),
        ("POST", "/api/jobs/import"),
        ("GET", "/api/job-suppressions"),
        ("DELETE", "/api/job-suppressions/{suppression_id}"),
        ("DELETE", "/api/job-suppressions"),
        ("GET", "/api/source-diagnostics"),
        ("GET", "/api/source-diagnostics/export"),
        ("GET", "/api/source-diagnostics/maintainer-report"),
        ("DELETE", "/api/source-diagnostics"),
        ("GET", "/api/saved-searches"),
        ("GET", "/api/saved-searches/due"),
        ("DELETE", "/api/saved-searches/{search_id}"),
    },
    "job-writes": {
        ("PATCH", "/api/jobs/{job_id}/lifecycle"),
        ("POST", "/api/jobs/{job_id}/lifecycle/undo"),
        ("POST", "/api/jobs/{job_id}/verify"),
    },
    "retention": {
        ("GET", "/api/jobs/cleanup-preview"),
        ("POST", "/api/jobs/cleanup"),
        ("DELETE", "/api/jobs/{job_id}"),
    },
    "portable-data-policy": {
        ("GET", "/api/data-export"),
        ("POST", "/api/account/erasure"),
        ("POST", "/api/full-backup"),
        ("POST", "/api/full-backup/restore"),
    },
    "generated-materials": {
        ("POST", "/api/jobs/{job_id}/tailor"),
        ("GET", "/api/jobs/{job_id}/tailored"),
        ("PATCH", "/api/jobs/{job_id}/tailored"),
        ("GET", "/api/jobs/{job_id}/materials/resume"),
        ("GET", "/api/jobs/{job_id}/materials/resume.docx"),
        ("GET", "/api/jobs/{job_id}/materials/cover-letter"),
        ("GET", "/api/jobs/{job_id}/apply-manually"),
    },
    "application-workspace": {
        ("GET", "/api/jobs/{job_id}/interview-prep"),
        ("PUT", "/api/jobs/{job_id}/interview-prep"),
        ("POST", "/api/jobs/{job_id}/interview-prep/generate"),
        ("GET", "/api/jobs/{job_id}/engagements"),
        ("POST", "/api/jobs/{job_id}/engagements"),
        ("PUT", "/api/jobs/{job_id}/engagements/{engagement_id}"),
        ("DELETE", "/api/jobs/{job_id}/engagements/{engagement_id}"),
    },
    "application-reads": {
        ("GET", "/api/applications"),
        ("GET", "/api/application-insights"),
    },
}


class SharedRoutePolicyInventoryTests(unittest.TestCase):
    def test_every_api_route_has_one_shared_host_policy(self) -> None:
        classified = [
            route
            for routes in SHARED_ROUTE_POLICIES.values()
            for route in routes
        ]
        self.assertEqual(len(classified), len(set(classified)))
        actual = {
            (method, route.path)
            for route in app_module.app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/")
            for method in route.methods
        }
        self.assertEqual(actual, set(classified))


class SearchPlanRoutePolicyTests(unittest.TestCase):
    """Exercise HTTP authentication and pure planning without a database fixture."""

    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(app_module.signup_http, "enabled", return_value=False))
        self.stack.enter_context(patch.object(app_module, "get_auth_configuration", return_value=Mock()))
        self.stack.enter_context(patch.object(app_module, "check_postgres_ready"))
        self.stack.enter_context(patch.object(app_module, "data_store_kind", return_value=app_module.SHARED_DATA_STORE))
        self.authentication = self.stack.enter_context(patch.object(
            app_module, "authenticate_session", return_value=SessionAuthentication(status="unauthenticated")
        ))
        self.authorization = self.stack.enter_context(patch.object(app_module, "authorized_local_user"))
        self.forbidden = []
        for name in ("get_db_connection", "runtime_profile_repository",
                     "runtime_search_run_repository", "runtime_discovery_repository",
                     "settings_from_profile", "current_provider_secret_configured"):
            self.forbidden.append(self.stack.enter_context(patch.object(
                app_module, name, side_effect=AssertionError(f"Planning must not call {name}")
            )))
        for target in ("discovery_sources.collect", "searcher.run_job_search_and_matching",
                       "searcher.extract_search_keywords_from_resume"):
            self.forbidden.append(self.stack.enter_context(patch(
                target, side_effect=AssertionError(f"Planning must not call {target}")
            )))
        self.client = TestClient(app_module.app, base_url="http://localhost")
        self.addCleanup(self.client.close)
        self.payload = {"keywords": "Data Engineer; Software Engineer - Data",
                        "title_alternatives": True, "location": "US remote"}

    def assert_no_work(self) -> None:
        for forbidden in self.forbidden:
            forbidden.assert_not_called()

    def authenticate(self, owner: int) -> None:
        self.authentication.return_value = SessionAuthentication(
            status="authenticated", workos_user_id=f"synthetic-{owner}", session_id=f"session-{owner}"
        )
        self.authorization.return_value = AuthorizedLocalUser(id=owner, role="user")

    def test_missing_session_rejected_before_planning_or_stores(self) -> None:
        with patch.object(app_module, "title_plan", side_effect=AssertionError("No unauthenticated plan")) as plan:
            response = self.client.post("/api/jobs/search/plan", json=self.payload)
        self.assertEqual(401, response.status_code, response.text)
        plan.assert_not_called()
        self.authorization.assert_not_called()
        self.assert_no_work()

    def test_two_authenticated_owners_get_only_submitted_plan_without_work(self) -> None:
        expected = {"mode": "manual", "titles": ["Data Engineer", "Software Engineer - Data"],
                    "max_titles": 3, "software_alias": False}
        for owner in (101, 202):
            with self.subTest(owner=owner):
                self.authenticate(owner)
                response = self.client.post("/api/jobs/search/plan", json={
                    **self.payload, "save_search": True, "saved_search_id": 7,
                })
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(expected, response.json())
        self.assert_no_work()

    def test_invalid_alternatives_rejected_without_work(self) -> None:
        self.authenticate(101)
        response = self.client.post("/api/jobs/search/plan", json={
            **self.payload, "keywords": "A;B;C;D",
        })
        self.assertEqual(422, response.status_code, response.text)
        self.assert_no_work()

class PostgreSQLCutoverRouteQualificationTests(unittest.TestCase):
    """Read-only route qualification; destructive flows retain focused suites."""

    @classmethod
    def setUpClass(cls) -> None:
        harness = postgres_harness.PostgreSQLTenantIsolationTests
        harness.setUpClass()
        cls._harness = harness
        cls.admin_engine = harness.admin_engine
        cls.runtime_engine = harness.runtime_engine
        cls.runtime_url = harness._role_url(
            postgres_harness.RUNTIME_ROLE,
            postgres_harness.RUNTIME_PASSWORD,
        ).render_as_string(hide_password=False)

    @classmethod
    def tearDownClass(cls) -> None:
        postgres_database_module.dispose_postgres_engines()
        cls._harness.tearDownClass()

    def setUp(self) -> None:
        postgres_harness.PostgreSQLTenantIsolationTests.setUp(self)
        self.sqlite_fallback = Mock(
            side_effect=AssertionError("shared route attempted a SQLite fallback")
        )
        self.object_keyring = object_store_module.ObjectKeyring(
            active_key_id="route-test-kek",
            keys={"route-test-kek": b"r" * 32},
        )
        encoded_keyring = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "route-test-kek": base64.urlsafe_b64encode(b"r" * 32).decode("ascii")
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        self.environment = patch.dict(
            os.environ,
            {
                "CAREERTRELLIS_ENVIRONMENT": "staging",
                "CAREERTRELLIS_ALLOWED_HOSTS": "shared.example.test",
                "CAREERTRELLIS_TRUSTED_PROXY_TOKEN": "p" * 40,
                "CAREERTRELLIS_DATA_STORE": "postgres",
                "CAREERTRELLIS_DATABASE_URL": self.runtime_url,
                "CAREERTRELLIS_PROVIDER_SECRET_STORE": SHARED_SECRET_STORE,
                "CAREERTRELLIS_OBJECT_STORE": "r2_encrypted",
                "CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID": "route-test-kek",
                "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64": encoded_keyring,
            },
            clear=False,
        )
        self.environment.start()
        postgres_database_module.dispose_postgres_engines()

        self.object_backend = object_store_module.InMemoryObjectBackend()
        self._seed_applications_and_objects()
        owner_by_identity = {
            "user_tenant_one": self.user_one,
            "user_tenant_two": self.user_two,
        }

        def authenticate(_settings: object, sealed_session: str) -> SessionAuthentication:
            if sealed_session not in owner_by_identity:
                return SessionAuthentication(status="unauthenticated")
            return SessionAuthentication(
                status="authenticated",
                workos_user_id=sealed_session,
                session_id=f"session-{sealed_session}",
            )

        def authorize(
            identity: str, _expected_owner: str, *, touch: bool = False
        ) -> AuthorizedLocalUser | None:
            del touch
            owner_id = owner_by_identity.get(identity)
            if owner_id is None:
                return None
            return AuthorizedLocalUser(
                id=owner_id,
                role="owner" if identity == "user_tenant_one" else "user",
            )

        self.patchers = [
            patch.object(app_module, "get_auth_configuration", return_value=Mock()),
            patch.object(app_module, "authenticate_session", side_effect=authenticate),
            patch.object(
                app_module,
                "authorized_local_user",
                side_effect=authorize,
            ),
            patch.object(app_module, "get_db_connection", self.sqlite_fallback),
            patch.object(app_module, "provider_secret_store_kind", return_value=SHARED_SECRET_STORE),
            patch.object(app_module, "current_provider_secret_configured", return_value=False),
            patch.object(app_module, "provider_key_configured", return_value=False),
            patch.object(app_module, "maps_provider_ready", return_value=False),
            patch.object(
                generated_material_module,
                "runtime_object_backend",
                return_value=self.object_backend,
            ),
            patch.object(
                stored_objects_module,
                "runtime_object_backend",
                return_value=self.object_backend,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(
            app_module.app,
            base_url="https://shared.example.test",
            headers={app_module.TRUSTED_PROXY_TOKEN_HEADER: "p" * 40},
        )

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        postgres_database_module.dispose_postgres_engines()
        self.environment.stop()

    def _seed_applications_and_objects(self) -> None:
        self.objects: dict[int, object_store_module.EncryptedStagedObject] = {}
        for owner_user_id in (self.user_one, self.user_two):
            staged = object_store_module.stage_object(
                self.object_backend,
                owner_user_id,
                f"Private material for owner {owner_user_id}".encode("utf-8"),
                "text/plain",
                keyring=self.object_keyring,
            )
            self.objects[owner_user_id] = staged
        self.resume_ids: dict[int, int] = {}
        with self.admin_engine.begin() as connection:
            for owner_user_id, job_id in (
                (self.user_one, self.job_one),
                (self.user_two, self.job_two),
            ):
                owner_label = "One" if owner_user_id == self.user_one else "Two"
                resume_id = connection.scalar(
                    text(
                        """INSERT INTO careertrellis.base_resumes
                           (owner_user_id, name, resume_mode, content)
                           VALUES (:owner, :name, 'general_professional', :content)
                           RETURNING id"""
                    ),
                    {
                        "owner": owner_user_id,
                        "name": f"Tenant {owner_label} Resume",
                        "content": f"Private resume content for tenant {owner_label}",
                    },
                )
                self.resume_ids[owner_user_id] = int(resume_id)
                connection.execute(
                    text(
                        """INSERT INTO careertrellis.base_resume_versions
                           (owner_user_id, base_resume_id, version_number, name,
                            resume_mode, content)
                           VALUES (:owner, :resume, 1, :name,
                                   'general_professional', :content)"""
                    ),
                    {
                        "owner": owner_user_id,
                        "resume": resume_id,
                        "name": f"Tenant {owner_label} Resume",
                        "content": f"Private resume content for tenant {owner_label}",
                    },
                )
                connection.execute(
                    text(
                        """UPDATE careertrellis.profiles
                           SET active_base_resume_id=:resume
                           WHERE owner_user_id=:owner"""
                    ),
                    {"resume": resume_id, "owner": owner_user_id},
                )
                staged = self.objects[owner_user_id]
                metadata = staged.metadata
                connection.execute(
                    text(
                        """INSERT INTO careertrellis.stored_objects
                           (id, owner_user_id, object_key, content_type,
                            plaintext_size, sha256, wrapped_data_key)
                           VALUES (:id, :owner, :key, :content_type,
                                   :size, :sha256, :wrapped)"""
                    ),
                    {
                        "id": metadata.id,
                        "owner": owner_user_id,
                        "key": metadata.object_key,
                        "content_type": metadata.content_type,
                        "size": metadata.size_bytes,
                        "sha256": bytes.fromhex(metadata.sha256),
                        "wrapped": metadata.wrapped_data_key,
                    },
                )
                connection.execute(
                    text(
                        """INSERT INTO careertrellis.applications
                           (owner_user_id, job_id, company, position, status,
                            tailored_resume_text, cover_letter,
                            cover_letter_object_id, interview_prep,
                            base_resume_id, base_resume_name, base_resume_version)
                           VALUES (:owner, :job, :company, :position, :status,
                                   'Synthetic resume', 'Synthetic letter',
                                   :object_id, 'Synthetic interview notes',
                                   :resume, :resume_name, 1)"""
                    ),
                    {
                        "owner": owner_user_id,
                        "job": job_id,
                        "company": f"Tenant {owner_user_id}",
                        "position": "Synthetic Role",
                        "status": "interview" if owner_user_id == self.user_one else "rejected",
                        "object_id": metadata.id,
                        "resume": resume_id,
                        "resume_name": f"Tenant {owner_label} Resume",
                    },
                )

    def _get(self, identity: str, path: str):
        return self.client.get(
            path,
            cookies={SESSION_COOKIE_NAME: identity},
            follow_redirects=False,
        )

    def test_representative_routes_use_postgres_for_both_owners(self) -> None:
        representative_paths = (
            "/api/session",
            "/api/profile",
            "/api/base-resumes",
            "/api/jobs",
            "/api/jobs/cleanup-preview",
            "/api/job-suppressions",
            "/api/jobs/status",
            "/api/source-diagnostics",
            "/api/source-diagnostics/export",
            "/api/source-diagnostics/maintainer-report",
            "/api/saved-searches",
            "/api/saved-searches/due",
            "/api/applications",
            "/api/application-insights",
            "/api/data-export",
        )
        for identity in ("user_tenant_one", "user_tenant_two"):
            for path in representative_paths:
                with self.subTest(identity=identity, path=path):
                    response = self._get(identity, path)
                    self.assertEqual(200, response.status_code, response.text)

        owner_one_jobs = self._get("user_tenant_one", "/api/jobs").json()
        owner_two_jobs = self._get("user_tenant_two", "/api/jobs").json()
        self.assertEqual([self.job_one], [row["id"] for row in owner_one_jobs])
        self.assertEqual([self.job_two], [row["id"] for row in owner_two_jobs])
        self.assertEqual("Tenant One", self._get("user_tenant_one", "/api/profile").json()["name"])
        self.assertEqual("Tenant Two", self._get("user_tenant_two", "/api/profile").json()["name"])

        owner_one_resumes = self._get("user_tenant_one", "/api/base-resumes").json()
        owner_two_resumes = self._get("user_tenant_two", "/api/base-resumes").json()
        self.assertEqual(
            ["Tenant One Resume"],
            [row["name"] for row in owner_one_resumes],
        )
        self.assertEqual(
            ["Tenant Two Resume"],
            [row["name"] for row in owner_two_resumes],
        )

        owner_one_applications = self._get("user_tenant_one", "/api/applications").json()
        owner_two_applications = self._get("user_tenant_two", "/api/applications").json()
        self.assertEqual(
            [(f"Tenant {self.user_one}", "interview")],
            [(row["company"], row["status"]) for row in owner_one_applications],
        )
        self.assertEqual(
            [(f"Tenant {self.user_two}", "rejected")],
            [(row["company"], row["status"]) for row in owner_two_applications],
        )

        owner_one_insights = self._get(
            "user_tenant_one", "/api/application-insights"
        ).json()["summary"]
        owner_two_insights = self._get(
            "user_tenant_two", "/api/application-insights"
        ).json()["summary"]
        self.assertEqual((1, 0), (owner_one_insights["interviews"], owner_one_insights["rejections"]))
        self.assertEqual((0, 1), (owner_two_insights["interviews"], owner_two_insights["rejections"]))

        owner_one_export = self._get("user_tenant_one", "/api/data-export").json()
        owner_two_export = self._get("user_tenant_two", "/api/data-export").json()
        self.assertEqual("Tenant One", owner_one_export["profile"]["name"])
        self.assertEqual("Tenant Two", owner_two_export["profile"]["name"])
        self.assertEqual(
            ["Tenant One Resume"],
            [row["name"] for row in owner_one_export["base_resumes"]],
        )
        self.assertEqual(
            ["Tenant Two Resume"],
            [row["name"] for row in owner_two_export["base_resumes"]],
        )
        self.assertNotIn("Tenant Two", json.dumps(owner_one_export, sort_keys=True))
        self.assertNotIn("Tenant One", json.dumps(owner_two_export, sort_keys=True))
        self.sqlite_fallback.assert_not_called()

    def test_generated_workspace_and_private_object_routes_are_owner_isolated(self) -> None:
        owner_one_materials = self._get(
            "user_tenant_one", f"/api/jobs/{self.job_one}/tailored"
        )
        self.assertEqual(200, owner_one_materials.status_code)
        self.assertTrue(owner_one_materials.json()["success"])
        cross_owner_materials = self._get(
            "user_tenant_one", f"/api/jobs/{self.job_two}/tailored"
        )
        self.assertEqual(200, cross_owner_materials.status_code)
        self.assertFalse(cross_owner_materials.json()["success"])

        workspace = self._get(
            "user_tenant_one", f"/api/jobs/{self.job_one}/interview-prep"
        )
        self.assertEqual(200, workspace.status_code, workspace.text)
        cross_owner_workspace = self._get(
            "user_tenant_one", f"/api/jobs/{self.job_two}/interview-prep"
        )
        self.assertEqual(404, cross_owner_workspace.status_code)

        owner_one_object = self.objects[self.user_one].metadata.id
        own_download = self._get(
            "user_tenant_one", f"/api/private-objects/{owner_one_object}"
        )
        self.assertEqual(200, own_download.status_code, own_download.text)
        self.assertEqual(
            f"Private material for owner {self.user_one}".encode("utf-8"),
            own_download.content,
        )
        cross_owner_download = self._get(
            "user_tenant_two", f"/api/private-objects/{owner_one_object}"
        )
        self.assertEqual(404, cross_owner_download.status_code)
        self.sqlite_fallback.assert_not_called()

    def test_provider_routes_use_owner_profile_before_mocked_external_calls(self) -> None:
        with (
            patch.object(app_module, "settings_from_profile", return_value=Mock()),
            patch.object(
                app_module,
                "validate_provider_capability",
                return_value={"success": True, "provider": "synthetic"},
            ) as ai_provider,
            patch.object(app_module, "maps_settings_from_profile", return_value=Mock()),
            patch.object(
                app_module,
                "validate_maps_provider",
                return_value={"success": True, "provider": "synthetic"},
            ) as maps_provider,
        ):
            for path in (
                "/api/profile/ai-provider/validate",
                "/api/profile/maps-provider/validate",
            ):
                response = self.client.post(
                    path,
                    cookies={SESSION_COOKIE_NAME: "user_tenant_one"},
                )
                self.assertEqual(200, response.status_code, response.text)
        ai_provider.assert_called_once()
        maps_provider.assert_called_once()
        self.sqlite_fallback.assert_not_called()

    def test_missing_session_stops_before_every_store(self) -> None:
        for path in (
            "/api/profile",
            "/api/jobs",
            "/api/data-export",
            f"/api/private-objects/{self.objects[self.user_one].metadata.id}",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(401, response.status_code, response.text)
        self.sqlite_fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
