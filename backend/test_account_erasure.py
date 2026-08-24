"""Synthetic tests for shared-host account erasure and its dedicated worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

import app as app_module
import account_erasure_execution as execution_module
import account_erasure_repository as repository_module
import account_erasure_worker as worker_module
import config as config_module
import data_store as data_store_module
import local_users as local_users_module
import provider_secrets as provider_secrets_module
import web_auth as web_auth_module


class AccountErasureRouteTests(unittest.TestCase):
    def request(self):
        return SimpleNamespace(state=SimpleNamespace(local_user_id=41))

    def payload(self):
        return app_module.AccountErasureRequest(
            confirmation="DELETE MY CAREERTRELLIS DATA",
            acknowledge_irreversible=True,
        )

    def assert_erasure_error_is_no_store(self, error: HTTPException) -> None:
        request = SimpleNamespace(url=SimpleNamespace(path="/api/account/erasure"))
        response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
        with patch.object(app_module.config, "get_runtime_environment", return_value="local"):
            secured = app_module._secured_response(request, response)
        self.assertEqual("no-store, max-age=0", secured.headers["cache-control"])

    def test_shared_request_returns_only_receipt_clears_session_and_never_calls_workos(self):
        repository = Mock()
        repository.request_erasure.return_value = {
            "status": "accepted",
            "accepted_at": "2026-08-14T01:02:03+00:00",
            "receipt_retention_days": 30,
            "owner_user_id": 41,
            "object_key": "v1/private-object-key",
        }
        request = self.request()
        with (
            patch.object(
                app_module,
                "data_store_kind",
                return_value=data_store_module.SHARED_DATA_STORE,
            ),
            patch.object(
                app_module,
                "runtime_account_erasure_repository",
                return_value=repository,
            ) as runtime_repository,
            patch.object(
                app_module.config,
                "account_erasure_worker_enabled",
                return_value=True,
            ),
            patch.object(app_module, "get_db_connection") as sqlite_connection,
            patch.object(app_module, "logout_url") as workos_logout,
        ):
            response = app_module.request_account_erasure(self.payload(), request)

        self.assertEqual(202, response.status_code)
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(
            {
                "status": "accepted",
                "accepted_at": "2026-08-14T01:02:03+00:00",
                "receipt_retention_days": 30,
            },
            json.loads(response.body),
        )
        cookie = response.headers["set-cookie"]
        self.assertIn("__Host-careertrellis_session=", cookie)
        self.assertIn("Max-Age=0", cookie)
        self.assertTrue(request.state.suppress_refreshed_session)
        runtime_repository.assert_called_once_with()
        repository.request_erasure.assert_called_once_with()
        sqlite_connection.assert_not_called()
        workos_logout.assert_not_called()

    def test_authenticated_middleware_suppresses_refresh_and_resets_owner_context(self):
        origin_token = "staging-origin-token-with-at-least-32-characters"
        environment = {
            "CAREERTRELLIS_ENVIRONMENT": "staging",
            "CAREERTRELLIS_ALLOWED_HOSTS": "staging-app.jhunterj.com",
            "CAREERTRELLIS_TRUSTED_PROXY_TOKEN": origin_token,
            "CAREERTRELLIS_AUTH_MODE": "workos",
            "CAREERTRELLIS_IDENTITY_STORE": "postgres",
            "CAREERTRELLIS_DATABASE_URL": "postgresql+psycopg://runtime:test@database.test/careertrellis",
            "WORKOS_API_KEY": "sk_test_ci_000000000000000000000000",
            "WORKOS_CLIENT_ID": "client_01STAGINGCLIENT",
            "WORKOS_COOKIE_PASSWORD": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "WORKOS_REDIRECT_URI": "https://staging-app.jhunterj.com/auth/callback",
            "WORKOS_LOGOUT_RETURN_TO": "https://staging-app.jhunterj.com/",
            "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID": "user_01STAGINGOWNER",
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED": "true",
        }
        authenticated = web_auth_module.SessionAuthentication(
            status="authenticated",
            workos_user_id="user_01STAGINGOWNER",
            session_id="session_01TEST",
            refreshed_session="private-refreshed-session",
        )
        repository = Mock()
        repository.request_erasure.return_value = {
            "status": "accepted",
            "accepted_at": "2026-08-14T01:02:03+00:00",
            "private": "must-not-serialize",
        }

        def selected_repository():
            self.assertEqual(41, provider_secrets_module.current_owner_user_id())
            return repository

        with (
            patch.dict(os.environ, environment, clear=False),
            patch.object(app_module, "data_store_kind", return_value=data_store_module.SHARED_DATA_STORE),
            patch.object(app_module, "authenticate_session", return_value=authenticated),
            patch.object(
                app_module,
                "authorized_local_user",
                return_value=local_users_module.AuthorizedLocalUser(41, "owner"),
            ),
            patch.object(app_module, "runtime_account_erasure_repository", side_effect=selected_repository),
            patch.object(app_module, "logout_url") as workos_logout,
        ):
            client = TestClient(
                app_module.app, base_url="https://staging-app.jhunterj.com"
            )
            try:
                response = client.post(
                    "/api/account/erasure",
                    headers={
                        app_module.TRUSTED_PROXY_TOKEN_HEADER: origin_token,
                        "Cookie": f"{web_auth_module.SESSION_COOKIE_NAME}=sealed-session",
                    },
                    json={
                        "confirmation": "DELETE MY CAREERTRELLIS DATA",
                        "acknowledge_irreversible": True,
                    },
                )
            finally:
                client.close()

        self.assertEqual(202, response.status_code)
        self.assertEqual(
            {
                "status": "accepted",
                "accepted_at": "2026-08-14T01:02:03+00:00",
                "receipt_retention_days": 30,
            },
            response.json(),
        )
        self.assertEqual("no-store, max-age=0", response.headers["cache-control"])
        session_cookies = [
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(web_auth_module.SESSION_COOKIE_NAME)
        ]
        self.assertEqual(1, len(session_cookies))
        self.assertIn("Max-Age=0", session_cookies[0])
        self.assertNotIn("private-refreshed-session", response.headers.get("set-cookie", ""))
        workos_logout.assert_not_called()
        with self.assertRaises(provider_secrets_module.ProviderSecretAuthorizationError):
            provider_secrets_module.current_owner_user_id()

    def test_local_mode_is_fixed_unsupported_before_sqlite_or_repository_access(self):
        repository_factory = Mock()
        sqlite_connection = Mock()
        with (
            patch.object(
                app_module,
                "data_store_kind",
                return_value=data_store_module.LOCAL_DATA_STORE,
            ),
            patch.object(
                app_module,
                "runtime_account_erasure_repository",
                repository_factory,
            ),
            patch.object(app_module, "get_db_connection", sqlite_connection),
            self.assertRaises(HTTPException) as caught,
        ):
            app_module.request_account_erasure(self.payload(), self.request())

        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual(app_module.LOCAL_ACCOUNT_ERASURE_DETAIL, caught.exception.detail)
        self.assert_erasure_error_is_no_store(caught.exception)
        repository_factory.assert_not_called()
        sqlite_connection.assert_not_called()

    def test_confirmation_and_irreversible_acknowledgement_are_exact(self):
        invalid_payloads = (
            {
                "confirmation": "delete my careertrellis data",
                "acknowledge_irreversible": True,
            },
            {
                "confirmation": "DELETE MY CAREERTRELLIS DATA",
                "acknowledge_irreversible": False,
            },
            {
                "confirmation": "DELETE MY CAREERTRELLIS DATA ",
                "acknowledge_irreversible": True,
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    app_module.AccountErasureRequest(**payload)

    def test_repository_failures_return_one_fixed_detail_without_private_data(self):
        private_detail = "postgresql://secret@private.test/v1/private-object"
        for failure in (
            app_module.AccountErasureUnavailable(),
            RuntimeError(private_detail),
        ):
            with self.subTest(failure=type(failure).__name__):
                repository = Mock()
                repository.request_erasure.side_effect = failure
                with (
                    patch.object(
                        app_module,
                        "data_store_kind",
                        return_value=data_store_module.SHARED_DATA_STORE,
                    ),
                    patch.object(
                        app_module,
                        "runtime_account_erasure_repository",
                        return_value=repository,
                    ),
                    patch.object(
                        app_module.config,
                        "account_erasure_worker_enabled",
                        return_value=True,
                    ),
                    self.assertRaises(HTTPException) as caught,
                ):
                    app_module.request_account_erasure(self.payload(), self.request())
                self.assertEqual(503, caught.exception.status_code)
                self.assertEqual(
                    app_module.ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
                    caught.exception.detail,
                )
                self.assertNotIn("private", caught.exception.detail)
                self.assert_erasure_error_is_no_store(caught.exception)

    def test_malformed_or_nonaccepted_receipts_fail_fixed_without_serializing_details(self):
        private_detail = "postgresql://secret@private.test/v1/private-object"
        malformed_receipts = (
            {"status": "queued", "accepted_at": "2026-08-14T01:02:03+00:00"},
            {"status": "accepted", "accepted_at": "2026-08-14T01:02:03"},
            {"status": "accepted", "accepted_at": private_detail},
            {"status": "accepted"},
        )
        for receipt in malformed_receipts:
            with self.subTest(receipt=receipt):
                repository = Mock()
                repository.request_erasure.return_value = receipt
                with (
                    patch.object(
                        app_module,
                        "data_store_kind",
                        return_value=data_store_module.SHARED_DATA_STORE,
                    ),
                    patch.object(
                        app_module,
                        "runtime_account_erasure_repository",
                        return_value=repository,
                    ),
                    patch.object(
                        app_module.config,
                        "account_erasure_worker_enabled",
                        return_value=True,
                    ),
                    self.assertRaises(HTTPException) as caught,
                ):
                    app_module.request_account_erasure(self.payload(), self.request())

                self.assertEqual(503, caught.exception.status_code)
                self.assertEqual(
                    repository_module.ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
                    caught.exception.detail,
                )
                self.assertNotIn(private_detail, caught.exception.detail)
                self.assert_erasure_error_is_no_store(caught.exception)

    def test_erasure_has_no_public_status_or_cancellation_route(self):
        erasure_routes = [
            (method, route.path)
            for route in app_module.app.routes
            if "erasure" in getattr(route, "path", "")
            for method in getattr(route, "methods", set())
        ]
        self.assertEqual([("POST", "/api/account/erasure")], erasure_routes)

    def test_session_summary_exposes_only_store_aware_erasure_availability(self):
        request = self.request()
        with (
            patch.object(app_module, "get_auth_configuration", return_value=None),
            patch.object(app_module, "data_store_kind") as selected_store,
        ):
            local_summary = app_module.get_session_summary(request)
        self.assertEqual(
            {
                "authentication": "local",
                "authenticated": True,
                "account_erasure_available": False,
            },
            local_summary,
        )
        selected_store.assert_not_called()

        with (
            patch.object(app_module, "get_auth_configuration", return_value=Mock()),
            patch.object(
                app_module,
                "data_store_kind",
                return_value=data_store_module.SHARED_DATA_STORE,
            ),
            patch.object(
                app_module.config,
                "account_erasure_worker_enabled",
                return_value=True,
            ),
        ):
            shared_summary = app_module.get_session_summary(request)
        self.assertEqual(
            {
                "authentication": "workos",
                "authenticated": True,
                "account_erasure_available": True,
            },
            shared_summary,
        )

        with (
            patch.object(app_module, "get_auth_configuration", return_value=Mock()),
            patch.object(
                app_module,
                "data_store_kind",
                return_value=data_store_module.LOCAL_DATA_STORE,
            ),
        ):
            workos_sqlite_summary = app_module.get_session_summary(request)
        self.assertEqual(
            {
                "authentication": "workos",
                "authenticated": True,
                "account_erasure_available": False,
            },
            workos_sqlite_summary,
        )

    def test_disabled_worker_hides_and_rejects_account_erasure_before_repository_access(self):
        repository_factory = Mock()
        with (
            patch.object(app_module, "get_auth_configuration", return_value=Mock()),
            patch.object(
                app_module,
                "data_store_kind",
                return_value=data_store_module.SHARED_DATA_STORE,
            ),
            patch.object(
                app_module.config,
                "account_erasure_worker_enabled",
                return_value=False,
            ),
            patch.object(
                app_module,
                "runtime_account_erasure_repository",
                repository_factory,
            ),
        ):
            summary = app_module.get_session_summary(self.request())
            with self.assertRaises(HTTPException) as caught:
                app_module.request_account_erasure(self.payload(), self.request())

        self.assertFalse(summary["account_erasure_available"])
        self.assertEqual(503, caught.exception.status_code)
        self.assertEqual(
            repository_module.ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE,
            caught.exception.detail,
        )
        repository_factory.assert_not_called()


class AccountErasureRepositoryTests(unittest.TestCase):
    def test_request_uses_one_owner_transaction_and_returns_minimized_receipt(self):
        accepted_at = datetime(2026, 8, 14, 1, 2, 3, tzinfo=timezone.utc)
        connection = Mock()
        connection.execute.return_value.mappings.return_value.one.return_value = {
            "accepted_at": accepted_at,
            "receipt_expires_at": None,
            "owner_user_id": 41,
            "request_id": str(uuid4()),
        }
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        engine = Mock()
        with patch.object(
            repository_module, "tenant_transaction", return_value=transaction
        ) as tenant_transaction:
            result = repository_module.PostgreSQLAccountErasureRepository(
                41, engine=engine
            ).request_erasure()

        self.assertEqual(
            {
                "status": "accepted",
                "accepted_at": accepted_at.isoformat(),
            },
            result,
        )
        tenant_transaction.assert_called_once_with(engine, 41)
        transaction.__exit__.assert_called_once()
        statement = str(connection.execute.call_args.args[0])
        self.assertEqual(
            "SELECT * FROM careertrellis.request_account_erasure()", statement
        )
        self.assertNotIn("owner_user_id", json.dumps(result))
        self.assertNotIn("request_id", json.dumps(result))

    def test_runtime_repository_is_shared_only_and_has_no_sqlite_fallback(self):
        engine = Mock()
        with (
            patch.object(
                repository_module,
                "data_store_kind",
                return_value=data_store_module.SHARED_DATA_STORE,
            ),
            patch.object(repository_module, "current_owner_user_id", return_value=41),
            patch.object(repository_module, "runtime_postgres_engine") as runtime_engine,
        ):
            selected = repository_module.runtime_account_erasure_repository(engine=engine)
        self.assertIsInstance(
            selected, repository_module.PostgreSQLAccountErasureRepository
        )
        self.assertIs(engine, selected._engine)
        runtime_engine.assert_not_called()

        with (
            patch.object(
                repository_module,
                "data_store_kind",
                return_value=data_store_module.LOCAL_DATA_STORE,
            ),
            patch.object(repository_module, "current_owner_user_id") as current_owner,
            patch.object(repository_module, "runtime_postgres_engine") as postgres_engine,
            self.assertRaises(repository_module.AccountErasureUnavailable),
        ):
            repository_module.runtime_account_erasure_repository()
        current_owner.assert_not_called()
        postgres_engine.assert_not_called()

    def test_repository_and_claim_failures_are_fixed_and_opaque(self):
        private_detail = "postgresql://secret@private.test/v1/private-object"
        transaction = MagicMock()
        transaction.__enter__.side_effect = SQLAlchemyError(private_detail)
        with (
            patch.object(
                repository_module, "tenant_transaction", return_value=transaction
            ),
            self.assertRaises(repository_module.AccountErasureUnavailable) as caught,
        ):
            repository_module.PostgreSQLAccountErasureRepository(
                41, engine=Mock()
            ).request_erasure()
        self.assertEqual(
            repository_module.ACCOUNT_ERASURE_UNAVAILABLE_MESSAGE, str(caught.exception)
        )
        self.assertNotIn("private", str(caught.exception))

        claim = repository_module.AccountErasureClaim(
            request_id=str(uuid4()),
            owner_user_id=41,
            lease_token=str(uuid4()),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        target = repository_module.AccountErasureObject(
            object_id=str(uuid4()), object_key="v1/private-object"
        )
        self.assertEqual("AccountErasureClaim(<opaque>)", repr(claim))
        self.assertEqual("AccountErasureObject(<opaque>)", repr(target))
        self.assertNotIn(claim.request_id, repr(claim))
        self.assertNotIn(target.object_key, repr(target))

    def test_claim_transaction_closes_before_worker_receives_private_targets(self):
        request_id = str(uuid4())
        lease_token = str(uuid4())
        lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        connection = Mock()
        connection.execute.return_value.mappings.return_value.first.return_value = {
            "request_id": request_id,
            "owner_user_id": 41,
            "lease_token": lease_token,
            "lease_expires_at": lease_expires_at,
        }
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        engine = Mock()
        engine.begin.return_value = transaction

        claim = repository_module.claim_next_account_erasure(
            str(uuid4()), engine=engine
        )

        self.assertEqual(request_id, claim.request_id)
        self.assertEqual(41, claim.owner_user_id)
        transaction.__exit__.assert_called_once()
        self.assertIn(
            "careertrellis.claim_next_account_erasure",
            str(connection.execute.call_args.args[0]),
        )

    def test_object_batch_transaction_closes_before_any_backend_delete(self):
        object_id = str(uuid4())
        target_row = {"object_id": object_id, "object_key": f"v1/{object_id.replace('-', '')}"}
        connection = Mock()
        connection.execute.return_value.mappings.return_value.all.return_value = [target_row]
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        engine = Mock()
        engine.begin.return_value = transaction
        claim = repository_module.AccountErasureClaim(
            request_id=str(uuid4()),
            owner_user_id=41,
            lease_token=str(uuid4()),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        targets = repository_module.PostgreSQLAccountErasureWorkerRepository(
            engine=engine
        ).object_batch(claim, limit=20)

        self.assertEqual((target_row["object_key"],), tuple(row.object_key for row in targets))
        transaction.__exit__.assert_called_once()


class AccountErasureExecutionTests(unittest.TestCase):
    def claim(self):
        return repository_module.AccountErasureClaim(
            request_id=str(uuid4()),
            owner_user_id=41,
            lease_token=str(uuid4()),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    def target(self):
        object_id = str(uuid4())
        return repository_module.AccountErasureObject(
            object_id=object_id,
            object_key=f"v1/{object_id.replace('-', '')}",
        )

    def test_delete_occurs_between_closed_repository_calls_then_finalizes(self):
        events: list[str] = []
        target = self.target()

        class Repository:
            def renew(self, _claim, *, lease_seconds):
                events.append(f"renew:{lease_seconds}")

            def object_batch(self, _claim, *, limit):
                events.append(f"batch:{limit}")
                return (target,)

            def confirm_object_deleted(self, _claim, confirmed):
                self_test.assertIs(target, confirmed)
                events.append("confirm")

            def finalize(self, _claim):
                events.append("finalize")
                return True

            def retry(self, *_args):
                self_test.fail("successful execution must not schedule retry")

        class Backend:
            def delete(self, object_key):
                self_test.assertEqual(target.object_key, object_key)
                events.append("delete")

        self_test = self
        result = execution_module.execute_account_erasure_claim(
            self.claim(),
            Repository(),
            backend=Backend(),
            batch_size=7,
            lease_seconds=60,
        )

        self.assertEqual("completed", result)
        self.assertEqual(
            ["renew:60", "batch:7", "renew:60", "delete", "confirm", "renew:60", "finalize"],
            events,
        )

    def test_object_failure_schedules_fixed_retry_without_confirm_or_private_detail(self):
        target = self.target()
        repository = Mock()
        repository.object_batch.return_value = (target,)
        private_detail = "v1/private-object postgresql://secret@private.test"
        backend = Mock()
        backend.delete.side_effect = RuntimeError(private_detail)

        result = execution_module.execute_account_erasure_claim(
            self.claim(), repository, backend=backend
        )

        self.assertEqual("retry_scheduled", result)
        repository.retry.assert_called_once()
        self.assertEqual("object_delete_failed", repository.retry.call_args.args[1])
        repository.confirm_object_deleted.assert_not_called()
        repository.finalize.assert_not_called()
        self.assertNotIn(private_detail, result)

    def test_stale_lease_stops_without_backend_or_retry(self):
        repository = Mock()
        repository.renew.side_effect = repository_module.AccountErasureLeaseLost()
        backend = Mock()

        result = execution_module.execute_account_erasure_claim(
            self.claim(), repository, backend=backend
        )

        self.assertEqual("lease_lost", result)
        backend.delete.assert_not_called()
        repository.retry.assert_not_called()

    def test_delete_backend_uses_only_delete_configuration_and_never_keyring(self):
        configuration = Mock()
        backend = Mock()
        with (
            patch.object(execution_module.config, "get_runtime_environment", return_value="production"),
            patch.dict(
                execution_module.os.environ,
                {execution_module.OBJECT_STORE_ENVIRONMENT_VARIABLE: execution_module.SHARED_OBJECT_STORE},
                clear=False,
            ),
            patch.object(
                execution_module,
                "r2_configuration_from_environment",
                return_value=configuration,
            ) as r2_configuration,
            patch.object(execution_module, "R2ObjectBackend", return_value=backend) as r2_backend,
        ):
            selected = execution_module.runtime_account_erasure_delete_backend()

        self.assertIs(backend, selected)
        r2_configuration.assert_called_once_with()
        r2_backend.assert_called_once_with(configuration)


class AccountErasureWorkerBoundaryTests(unittest.TestCase):
    def test_disabled_default_never_opens_postgres_or_claims(self):
        worker_module.STOP_EVENT.clear()
        with (
            patch.object(worker_module.config, "validate_account_erasure_worker_configuration", return_value={"environment": "staging"}),
            patch.object(worker_module.config, "get_account_erasure_poll_seconds", return_value=5),
            patch.object(worker_module.config, "get_account_erasure_batch_size", return_value=20),
            patch.object(worker_module.config, "account_erasure_worker_enabled", return_value=False),
            patch.object(worker_module, "_write_heartbeat") as heartbeat,
            patch.object(worker_module, "runtime_postgres_engine") as postgres_engine,
            patch.object(worker_module, "claim_next_account_erasure") as claim,
            patch("builtins.print"),
        ):
            self.assertEqual(0, worker_module.run(once=True))

        heartbeat.assert_called_once()
        self.assertEqual("idle_disabled", heartbeat.call_args.args[1])
        postgres_engine.assert_not_called()
        claim.assert_not_called()

    def test_persistence_outage_is_not_overwritten_by_idle_heartbeat(self):
        worker_module.STOP_EVENT.clear()
        engine = Mock()
        repository = Mock()
        with (
            patch.object(
                worker_module.config,
                "validate_account_erasure_worker_configuration",
                return_value={"environment": "staging"},
            ),
            patch.object(
                worker_module.config,
                "get_account_erasure_poll_seconds",
                return_value=5,
            ),
            patch.object(
                worker_module.config,
                "get_account_erasure_batch_size",
                return_value=10,
            ),
            patch.object(
                worker_module.config,
                "account_erasure_worker_enabled",
                return_value=True,
            ),
            patch.object(
                worker_module,
                "validate_data_store_configuration",
                return_value=data_store_module.SHARED_DATA_STORE,
            ),
            patch.object(worker_module, "runtime_postgres_engine", return_value=engine),
            patch.object(
                worker_module,
                "PostgreSQLAccountErasureWorkerRepository",
                return_value=repository,
            ),
            patch.object(worker_module, "runtime_account_erasure_delete_backend"),
            patch.object(
                worker_module,
                "claim_next_account_erasure",
                side_effect=worker_module.AccountErasureUnavailable(),
            ) as claim,
            patch.object(worker_module, "_write_heartbeat") as heartbeat,
            patch("builtins.print"),
        ):
            self.assertEqual(0, worker_module.run(once=True))

        claim.assert_called_once()
        self.assertEqual(1, heartbeat.call_count)
        self.assertEqual("persistence_unavailable", heartbeat.call_args.args[1])

    def test_configuration_rejects_web_workos_provider_and_encryption_credentials(self):
        forbidden_names = (
            "CAREERTRELLIS_TRUSTED_PROXY_TOKEN",
            "WORKOS_API_KEY",
            "GEMINI_API_KEY",
            "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64",
        )
        for name in forbidden_names:
            with self.subTest(name=name), patch.dict(
                config_module.os.environ,
                {name: "private-value", "CAREERTRELLIS_ENVIRONMENT": "staging"},
                clear=True,
            ), self.assertRaises(config_module.RuntimeConfigurationError) as caught:
                config_module.validate_account_erasure_worker_configuration()
            self.assertEqual(
                "The account-erasure worker environment contains a credential or setting outside its delete-only boundary.",
                str(caught.exception),
            )

    def test_enabled_configuration_requires_exact_erasure_database_role(self):
        environment = {
            "CAREERTRELLIS_ENVIRONMENT": "staging",
            "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED": "true",
            "CAREERTRELLIS_OBJECT_STORE": "r2_encrypted",
        }
        with (
            patch.dict(config_module.os.environ, environment, clear=True),
            patch(
                "postgres_database.database_url_from_environment",
                return_value=SimpleNamespace(username="careertrellis_runtime"),
            ),
            self.assertRaises(config_module.RuntimeConfigurationError) as caught,
        ):
            config_module.validate_account_erasure_worker_configuration()
        self.assertEqual(
            "The account-erasure worker requires its dedicated PostgreSQL role.",
            str(caught.exception),
        )

        with (
            patch.dict(config_module.os.environ, environment, clear=True),
            patch(
                "postgres_database.database_url_from_environment",
                return_value=SimpleNamespace(username="careertrellis_erasure_worker"),
            ),
        ):
            summary = config_module.validate_account_erasure_worker_configuration()
        self.assertEqual({"environment": "staging", "enabled": True}, summary)

    def test_migration_preserves_ownerless_receipt_and_has_no_workos_delete(self):
        source = (
            Path(__file__).with_name("migrations")
            / "versions"
            / "20260814_0009_account_erasure.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SESSION_USER", source)
        self.assertIn("DELETE FROM careertrellis.app_users", source)
        self.assertIn("INTERVAL '30 days'", source)
        self.assertIn("purge_expired_account_erasures", source)
        self.assertNotIn("workos.", source.lower())
        self.assertNotIn("delete_user(", source.lower())


class AccountErasureDeploymentBoundaryTests(unittest.TestCase):
    @classmethod
    def project_dir(cls) -> Path:
        return Path(__file__).resolve().parents[1]

    @staticmethod
    def environment_names(source: str) -> set[str]:
        return {
            line.split("=", 1)[0]
            for line in source.splitlines()
            if line and not line.startswith("#") and "=" in line
        }

    @staticmethod
    def activation_service_slice(source: str) -> str:
        start = source.index("    systemctl daemon-reload || return 1")
        readiness_loop = source.index("    for _attempt in $(seq 1 30); do", start)
        end_marker = "\n    return 1\n}"
        end = source.index(end_marker, readiness_loop) + len("\n    return 1")
        return source[start:end]

    def test_runtime_grants_revoke_private_erasure_tables_after_broad_grants(self):
        grant_source = (
            self.project_dir() / "deploy" / "postgres" / "grant-runtime.sql"
        ).read_text(encoding="utf-8")
        broad_grant = grant_source.index(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA careertrellis"
        )
        private_revoke = grant_source.index(
            "REVOKE ALL ON careertrellis.account_erasure_requests,\n"
            "    careertrellis.account_erasure_claim_queue\n"
            "    FROM careertrellis_runtime, careertrellis_worker,\n"
            "    careertrellis_erasure_worker;"
        )

        self.assertGreater(private_revoke, broad_grant)
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION careertrellis.request_account_erasure()\n"
            "    TO careertrellis_runtime;",
            grant_source,
        )
        self.assertIn(
            "careertrellis.purge_expired_account_erasures(integer)\n"
            "    TO careertrellis_erasure_worker;",
            grant_source,
        )

    def test_release_tools_accept_exact_boolean_without_activating_worker(self):
        scripts = self.project_dir() / "deploy" / "scripts"
        enablement_pattern = (
            r"^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=(true|false)$"
        )
        for name in ("deploy-release.sh", "rollback-release.sh"):
            with self.subTest(script=name):
                source = (scripts / name).read_text(encoding="utf-8")
                self.assertIn(f"grep -Ec '{enablement_pattern}'", source)
                self.assertNotIn(
                    'grep -Fxc "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false"',
                    source,
                )
                self.assertIn(
                    "systemctl is-active --quiet careertrellis-erasure-worker.service",
                    source,
                )
                self.assertIn(
                    "systemctl restart careertrellis-erasure-worker.service",
                    source,
                )
                self.assertIn(
                    "${web_search_worker_enabled} != \"${search_worker_enabled}\"",
                    source,
                )
                self.assertIn(
                    "${web_erasure_worker_enabled} != \"${erasure_worker_enabled}\"",
                    source,
                )
                self.assertIn(
                    "if [[ ${search_worker_enabled} == true ]]; then",
                    source,
                )
                self.assertIn(
                    "systemctl disable careertrellis-worker.service",
                    source,
                )
                self.assertIn(
                    "systemctl disable careertrellis-erasure-worker.service",
                    source,
                )
                if name == "deploy-release.sh":
                    self.assertIn(
                        'systemctl is-enabled --quiet "${unit}"', source
                    )
                    self.assertNotIn(
                        "assert_erasure_worker_activation_ready", source
                    )
                    preflight_command = (
                        'assert_consumer_activation_ready "${erasure_worker_enabled}" '
                        "careertrellis-erasure-worker.service"
                    )
                else:
                    self.assertIn(
                        "systemctl is-enabled --quiet "
                        "careertrellis-erasure-worker.service",
                        source,
                    )
                    preflight_command = (
                        'assert_erasure_worker_activation_ready '
                        '"${erasure_worker_enabled}"'
                    )
                self.assertIn(preflight_command, source)
                activation_preflight = source.index(preflight_command)
                activation_link = source.index("    activation_link=", activation_preflight)
                self.assertLess(activation_preflight, activation_link)
                if name == "deploy-release.sh":
                    self.assertLess(
                        source.index('validate_release "${release_dir}"'),
                        source.index(
                            "install -d -o root -g root -m 0755 "
                            "/usr/local/libexec/careertrellis"
                        ),
                    )
                web_restart = source.index(
                    "systemctl restart careertrellis-web.service"
                )
                web_stop = source.index(
                    "systemctl stop careertrellis-web.service || return 1",
                    activation_link,
                )
                self.assertLess(web_stop, web_restart)
                for command in (
                    "systemctl disable careertrellis-worker.service",
                    "systemctl stop careertrellis-worker.service",
                    "systemctl disable careertrellis-erasure-worker.service",
                    "systemctl stop careertrellis-erasure-worker.service",
                ):
                    command_position = source.index(command)
                    self.assertLess(web_stop, command_position)
                    self.assertLess(command_position, web_restart)
                self.assertNotIn(
                    "systemctl enable --now careertrellis-erasure-worker.service",
                    source,
                )

        exact_enablement = re.compile(enablement_pattern)
        for value in ("true", "false"):
            self.assertIsNotNone(
                exact_enablement.fullmatch(
                    f"CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED={value}"
                )
            )
        for value in ("TRUE", "False", " true", "true ", "1", ""):
            self.assertIsNone(
                exact_enablement.fullmatch(
                    f"CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED={value}"
                )
            )

    @unittest.skipIf(os.name == "nt", "POSIX release service-state regression")
    def test_true_erasure_switch_requires_reviewed_service_state(self):
        scripts = self.project_dir() / "deploy" / "scripts"
        for name in ("deploy-release.sh", "rollback-release.sh"):
            source = (scripts / name).read_text(encoding="utf-8")
            if name == "deploy-release.sh":
                function_name = "assert_consumer_activation_ready"
                states = (
                    ("false", 1, 1, 0),
                    ("true", 0, 0, 0),
                    ("true", 1, 0, 0),
                    ("true", 0, 1, 1),
                )
                invocation = (
                    "assert_consumer_activation_ready "
                    "{configured} careertrellis-erasure-worker.service\n"
                )
            else:
                function_name = "assert_erasure_worker_activation_ready"
                states = (
                    ("false", 1, 1, 0),
                    ("true", 0, 0, 0),
                    ("true", 1, 0, 1),
                    ("true", 0, 1, 1),
                )
                invocation = (
                    "assert_erasure_worker_activation_ready {configured}\n"
                )
            start = source.index(f"{function_name}() {{")
            end = source.index("\n}\n", start) + len("\n}")
            preflight = source[start:end]
            for configured, active_status, enabled_status, expected in states:
                with self.subTest(
                    script=name,
                    configured=configured,
                    active_status=active_status,
                    enabled_status=enabled_status,
                ):
                    script = (
                        "set -Euo pipefail\n"
                        f"ACTIVE_STATUS={active_status}\n"
                        f"ENABLED_STATUS={enabled_status}\n"
                        "systemctl() {\n"
                        "case \"$1\" in\n"
                        "is-active) return \"${ACTIVE_STATUS}\" ;;\n"
                        "is-enabled) return \"${ENABLED_STATUS}\" ;;\n"
                        "*) return 97 ;;\n"
                        "esac\n"
                        "}\n"
                        f"{preflight}\n"
                        f"{invocation.format(configured=configured)}"
                    )
                    result = subprocess.run(
                        ["bash", "-c", script],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(expected, result.returncode, result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX release service-state regression")
    def test_disabled_worker_branches_stop_and_disable_both_services(self):
        scripts = self.project_dir() / "deploy" / "scripts"
        for name in ("deploy-release.sh", "rollback-release.sh"):
            with self.subTest(script=name), tempfile.TemporaryDirectory() as temp_dir:
                source = (scripts / name).read_text(encoding="utf-8")
                activation_slice = self.activation_service_slice(source)
                event_log = Path(temp_dir) / "events"
                script = (
                    "set -Eeuo pipefail\n"
                    f"EVENT_LOG='{event_log.as_posix()}'\n"
                    "systemctl() { printf '%s\\n' \"$*\" >> \"${EVENT_LOG}\"; }\n"
                    "curl() { return 0; }\n"
                    "seq() { printf '1\\n'; }\n"
                    "activate_release_slice() {\n"
                    "local search_worker_enabled=false\n"
                    "local erasure_worker_enabled=false\n"
                    "local recovery_helpers_available=true\n"
                    f"{activation_slice}\n"
                    "}\n"
                    "activate_release_slice\n"
                )
                result = subprocess.run(
                    ["bash", "-c", script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                events = event_log.read_text(encoding="utf-8").splitlines()
                self.assertIn("restart careertrellis-web.service", events)
                self.assertIn("stop careertrellis-web.service", events)
                self.assertIn("stop careertrellis-worker.service", events)
                self.assertIn("stop careertrellis-erasure-worker.service", events)
                self.assertIn("enable careertrellis-web.service", events)
                self.assertIn("disable careertrellis-worker.service", events)
                self.assertIn("disable careertrellis-erasure-worker.service", events)
                self.assertNotIn("restart careertrellis-worker.service", events)
                self.assertNotIn(
                    "restart careertrellis-erasure-worker.service", events
                )
                web_restart = events.index("restart careertrellis-web.service")
                web_stop = events.index("stop careertrellis-web.service")
                self.assertLess(
                    web_stop, events.index("disable careertrellis-worker.service")
                )
                self.assertLess(
                    web_stop, events.index("stop careertrellis-worker.service")
                )
                self.assertLess(
                    web_stop,
                    events.index("disable careertrellis-erasure-worker.service"),
                )
                self.assertLess(
                    web_stop,
                    events.index("stop careertrellis-erasure-worker.service"),
                )
                for command in (
                    "disable careertrellis-worker.service",
                    "stop careertrellis-worker.service",
                    "disable careertrellis-erasure-worker.service",
                    "stop careertrellis-erasure-worker.service",
                ):
                    self.assertLess(events.index(command), web_restart)

    @unittest.skipIf(os.name == "nt", "POSIX release service-state regression")
    def test_readiness_failure_leaves_false_workers_stopped_and_disabled(self):
        scripts = self.project_dir() / "deploy" / "scripts"
        for name in ("deploy-release.sh", "rollback-release.sh"):
            with self.subTest(script=name), tempfile.TemporaryDirectory() as temp_dir:
                source = (scripts / name).read_text(encoding="utf-8")
                activation_slice = self.activation_service_slice(source)
                event_log = Path(temp_dir) / "events"
                script = (
                    "set -Euo pipefail\n"
                    f"EVENT_LOG='{event_log.as_posix()}'\n"
                    "systemctl() { printf '%s\\n' \"$*\" >> \"${EVENT_LOG}\"; }\n"
                    "curl() { return 1; }\n"
                    "sleep() { :; }\n"
                    "seq() { printf '1\\n'; }\n"
                    "activate_release_slice() {\n"
                    "local search_worker_enabled=false\n"
                    "local erasure_worker_enabled=false\n"
                    "local recovery_helpers_available=true\n"
                    f"{activation_slice}\n"
                    "}\n"
                    "activate_release_slice\n"
                )
                result = subprocess.run(
                    ["bash", "-c", script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(1, result.returncode, result.stderr)
                events = event_log.read_text(encoding="utf-8").splitlines()
                web_restart = events.index("restart careertrellis-web.service")
                web_stop = events.index("stop careertrellis-web.service")
                for command in (
                    "disable careertrellis-worker.service",
                    "stop careertrellis-worker.service",
                    "disable careertrellis-erasure-worker.service",
                    "stop careertrellis-erasure-worker.service",
                ):
                    self.assertLess(web_stop, events.index(command))
                    self.assertLess(events.index(command), web_restart)
                self.assertNotIn("enable careertrellis-web.service", events)
                self.assertNotIn("restart careertrellis-worker.service", events)
                self.assertNotIn(
                    "restart careertrellis-erasure-worker.service", events
                )

    @unittest.skipIf(os.name == "nt", "POSIX release service-state regression")
    def test_enabled_search_branch_still_restarts_and_enables_search_worker(self):
        scripts = self.project_dir() / "deploy" / "scripts"
        for name in ("deploy-release.sh", "rollback-release.sh"):
            with self.subTest(script=name), tempfile.TemporaryDirectory() as temp_dir:
                source = (scripts / name).read_text(encoding="utf-8")
                activation_slice = self.activation_service_slice(source)
                event_log = Path(temp_dir) / "events"
                script = (
                    "set -Eeuo pipefail\n"
                    f"EVENT_LOG='{event_log.as_posix()}'\n"
                    "systemctl() { printf '%s\\n' \"$*\" >> \"${EVENT_LOG}\"; }\n"
                    "curl() { return 0; }\n"
                    "seq() { printf '1\\n'; }\n"
                    "activate_release_slice() {\n"
                    "local search_worker_enabled=true\n"
                    "local erasure_worker_enabled=false\n"
                    "local recovery_helpers_available=true\n"
                    f"{activation_slice}\n"
                    "}\n"
                    "activate_release_slice\n"
                )
                result = subprocess.run(
                    ["bash", "-c", script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                events = event_log.read_text(encoding="utf-8").splitlines()
                self.assertLess(
                    events.index("stop careertrellis-web.service"),
                    events.index("stop careertrellis-erasure-worker.service"),
                )
                self.assertIn("restart careertrellis-worker.service", events)
                self.assertIn("enable careertrellis-worker.service", events)
                self.assertNotIn("disable careertrellis-worker.service", events)

    def test_erasure_role_bootstrap_streams_root_read_sql_without_password_argv(self):
        script = (
            self.project_dir()
            / "deploy"
            / "scripts"
            / "bootstrap-erasure-worker-role.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('cat -- "${bootstrap_sql}"', script)
        self.assertNotIn(r"\i ${bootstrap_sql}", script)
        self.assertIn(
            r"\getenv erasure_worker_password CAREERTRELLIS_ERASURE_WORKER_PASSWORD",
            script,
        )
        self.assertIn("--preserve-environment", script)
        self.assertNotRegex(
            script,
            r"--(?:set|variable)(?:=|\s+)[^\n]*erasure_worker_password",
        )
        self.assertNotIn("set -x", script)

        password_generation = script.index(
            "export CAREERTRELLIS_ERASURE_WORKER_PASSWORD="
        )
        credential_file_write = script.index(
            "printf 'CAREERTRELLIS_ERASURE_WORKER_PASSWORD=%s\\n'"
        )
        bootstrap_invocation = script[password_generation:credential_file_write]
        self.assertIn('cat -- "${bootstrap_sql}"', bootstrap_invocation)
        self.assertNotIn("sha256sum", bootstrap_invocation)
        self.assertNotRegex(
            bootstrap_invocation,
            r"(?:echo|printf)[^\n]*\$\{CAREERTRELLIS_ERASURE_WORKER_PASSWORD\}",
        )

    def test_erasure_credential_installer_retains_permissions_and_no_activation(self):
        script = (
            self.project_dir()
            / "deploy"
            / "scripts"
            / "install-erasure-worker-credential.sh"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "install -d -o root -g root -m 0755 /etc/careertrellis",
            script,
        )
        self.assertIn(
            'install -o root -g root -m 0600 "${environment_temporary}" "${target_environment}"',
            script,
        )
        self.assertIn(
            'install -o root -g root -m 0600 "${encrypted_bundle}" "${retained_bundle}"',
            script,
        )
        self.assertIn("chmod 0600 \"${plaintext}\"", script)
        self.assertIn("chmod 0600 \"${environment_temporary}\"", script)
        self.assertIn("careertrellis_erasure_worker:%s@%s:5432/%s", script)
        self.assertIn('authenticated_role=$(PGPASSWORD="${password}" psql', script)
        self.assertIn("--no-password", script)
        self.assertNotRegex(script, r"--password(?:=|\s)")
        self.assertNotIn("set -x", script)
        for service_action in (
            "systemctl enable",
            "systemctl start",
            "systemctl restart",
            "systemctl try-restart",
        ):
            with self.subTest(service_action=service_action):
                self.assertNotIn(service_action, script)

    def test_shared_only_ui_uses_exact_irreversible_warning_and_request_contract(self):
        static = self.project_dir() / "backend" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        javascript = (static / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="open-account-erasure-btn" hidden', html)
        self.assertIn("This request cannot be cancelled.", html)
        self.assertIn("Your WorkOS identity is not deleted.", html)
        self.assertIn("No WorkOS account deletion is requested.", html)
        self.assertIn("DELETE MY CAREERTRELLIS DATA", html)
        self.assertIn('session.account_erasure_available !== true', javascript)
        self.assertIn('accountErasureConfirmation.value !== "DELETE MY CAREERTRELLIS DATA"', javascript)
        self.assertIn("`${API_URL}/api/account/erasure`", javascript)
        self.assertIn('method: "POST"', javascript)

    def test_worker_loads_only_dedicated_database_and_delete_only_object_credentials(self):
        deploy = self.project_dir() / "deploy"
        service = (
            deploy / "systemd" / "careertrellis-erasure-worker.service"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            [
                "EnvironmentFile=/etc/careertrellis/account-erasure-worker.env",
                "EnvironmentFile=/etc/careertrellis/postgres-erasure-worker.env",
                "EnvironmentFile=/etc/careertrellis/account-erasure-object-storage.env",
            ],
            [
                line
                for line in service.splitlines()
                if line.startswith("EnvironmentFile=")
            ],
        )
        for forbidden in (
            "staging.env",
            "postgres-runtime.env",
            "postgres-worker.env",
            "provider-secrets.env",
            "object-storage.env",
        ):
            self.assertNotIn(f"/etc/careertrellis/{forbidden}", service)
        self.assertIn("User=careertrellis-erasure-worker", service)
        self.assertIn("ProtectSystem=strict", service)

        worker_environment = (
            deploy / "config" / "account-erasure-worker.environment.example"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            {
                "CAREERTRELLIS_ENVIRONMENT",
                "CAREERTRELLIS_DATA_DIR",
                "CAREERTRELLIS_DATA_STORE",
                "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED",
                "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS",
                "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE",
            },
            self.environment_names(worker_environment),
        )
        self.assertIn("CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false", worker_environment)
        self.assertIn("CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE=10", worker_environment)

        object_environment = (
            deploy / "config" / "account-erasure-object-storage.environment.example"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            {
                "CAREERTRELLIS_OBJECT_STORE",
                "CAREERTRELLIS_OBJECT_STORE_ENDPOINT",
                "CAREERTRELLIS_OBJECT_STORE_BUCKET",
                "CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID",
                "CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY",
            },
            self.environment_names(object_environment),
        )
        self.assertNotIn("KEYRING", object_environment)
        self.assertNotIn("ACTIVE_KEY_ID", object_environment)
        self.assertNotIn("WORKOS", object_environment)


if __name__ == "__main__":
    unittest.main()
