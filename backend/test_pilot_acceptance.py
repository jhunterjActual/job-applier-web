from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from datetime import datetime, timedelta, timezone

import pilot_acceptance as acceptance
import object_store


RELEASE = "a" * 40
BUILD = "20260813.39"


class PilotAcceptanceStateTests(unittest.TestCase):
    def state(self) -> acceptance.AcceptanceState:
        return acceptance.AcceptanceState.new(
            release=RELEASE,
            build=BUILD,
            owner_user_id=41,
            pilot_user_id=42,
            provider="gemini",
        )

    def test_state_round_trip_is_atomic_strict_and_contains_no_external_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = acceptance.StateStore(Path(directory) / "state", strict_metadata=False)
            state = self.state()
            store.write(state, create=True)
            loaded = store.read()
            self.assertEqual(state, loaded)
            content = store.path.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"(?<![A-Za-z0-9_])user_[A-Za-z0-9_]{6,120}", content)
            )
            self.assertNotIn("@", content)
            self.assertEqual([], list(store.root.glob("*.tmp")))
            with self.assertRaises(acceptance.PilotAcceptanceError):
                store.write(state, create=True)

    def test_state_rejects_unknown_fields_checks_and_invalid_transitions(self) -> None:
        document = self.state().document()
        document["unexpected"] = True
        with self.assertRaises(acceptance.PilotAcceptanceError):
            acceptance.AcceptanceState.from_mapping(document)

        state = self.state()
        with self.assertRaises(acceptance.PilotAcceptanceError):
            state.transition("automated_partial")
        fixture = acceptance.FixtureIds(*range(1, 12))
        prepared = state.transition("prepared", fixture=fixture.__dict__)
        with self.assertRaises(acceptance.PilotAcceptanceError):
            prepared.transition("browser_recorded")
        with self.assertRaises(acceptance.PilotAcceptanceError):
            acceptance.MappingPhaseEvidence.from_mapping({
                "disabled_verified": False,
                "recovered_verified": True,
            })

    def test_state_store_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            root.mkdir()
            target = root / "target"
            target.write_text("{}", encoding="utf-8")
            try:
                os.symlink(target, root / "active.json")
            except (NotImplementedError, OSError):
                self.skipTest("The test host cannot create symlinks.")
            store = acceptance.StateStore(root, strict_metadata=False)
            with self.assertRaises(acceptance.PilotAcceptanceError):
                store.read()

    def test_disabled_and_recovered_evidence_is_typed_and_persisted(self) -> None:
        fixture = acceptance.FixtureIds(*range(1, 12))
        state = self.state().transition(
            "prepared", fixture=fixture.__dict__
        ).transition(
            "automated_partial",
            mapping_evidence={
                "disabled_verified": True,
                "recovered_verified": False,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            store = acceptance.StateStore(
                Path(directory) / "state", strict_metadata=False
            )
            store.write(state, create=True)
            disabled = store.read()
            self.assertTrue(disabled.mapping_evidence.disabled_verified)
            self.assertFalse(disabled.mapping_evidence.recovered_verified)
            recovered = disabled.transition(
                "automated_partial",
                mapping_evidence={
                    "disabled_verified": True,
                    "recovered_verified": True,
                },
            )
            store.write(recovered)
            self.assertEqual(
                acceptance.MappingPhaseEvidence(True, True),
                store.read().mapping_evidence,
            )


class ProtectedEnvironmentTests(unittest.TestCase):
    @staticmethod
    def encoded_keyring(key_id: str) -> str:
        encoded_key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
        document = json.dumps(
            {key_id: encoded_key}, separators=(",", ":")
        ).encode("utf-8")
        return base64.urlsafe_b64encode(document).decode("ascii")

    def test_parser_selects_required_values_without_evaluating_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "protected.env"
            path.write_text(
                "# comment\nREQUIRED=literal-$-value\nIGNORED=safe\nSENTRY_DSN=\n",
                encoding="utf-8",
            )
            self.assertEqual(
                {"REQUIRED": "literal-$-value"},
                acceptance.read_protected_environment(
                    path, ("REQUIRED",), strict_metadata=False
                ),
            )

    def test_parser_rejects_duplicates_whitespace_missing_and_symlinks(self) -> None:
        samples = (
            "REQUIRED=one\nREQUIRED=two\n",
            "REQUIRED=\n",
            "REQUIRED=\nREQUIRED=two\n",
            "REQUIRED=one\nREQUIRED=\n",
            "REQUIRED=has space\n",
            "OTHER=value\n",
            "REQUIRED=value\nOPTIONAL= \n",
            "REQUIRED=value\nOPTIONAL=has\tspace\n",
            "REQUIRED=value\nOPTIONAL\n",
            "REQUIRED=value\ninvalid-key=\n",
        )
        for sample in samples:
            with self.subTest(sample=sample), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "protected.env"
                path.write_text(sample, encoding="utf-8")
                with self.assertRaises(acceptance.PilotAcceptanceError):
                    acceptance.read_protected_environment(
                        path, ("REQUIRED",), strict_metadata=False
                    )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("REQUIRED=value\n", encoding="utf-8")
            link = Path(directory) / "link"
            try:
                os.symlink(target, link)
            except (NotImplementedError, OSError):
                self.skipTest("The test host cannot create symlinks.")
            with self.assertRaises(acceptance.PilotAcceptanceError):
                acceptance.read_protected_environment(
                    link, ("REQUIRED",), strict_metadata=False
                )

    def test_expected_worker_pid_environment_is_fixed_and_canonical(self) -> None:
        key = acceptance.SEARCH_WORKER_PID_ENVIRONMENT_KEY
        with patch.dict(os.environ, {key: "123"}, clear=False):
            self.assertEqual(123, acceptance._expected_pid_from_environment(key))
        for value in ("", "0", "01", "+2", "pid"):
            with self.subTest(value=value), patch.dict(
                os.environ, {key: value}, clear=False
            ):
                with self.assertRaises(acceptance.PilotAcceptanceError):
                    acceptance._expected_pid_from_environment(key)

    def test_expected_worker_file_owner_matches_fixed_environment(self) -> None:
        values = {
            acceptance.SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY: "123",
            acceptance.SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY: "456",
            acceptance.SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY: "600",
        }
        with patch.dict(os.environ, values, clear=False), patch.object(
            acceptance, "_worker_file_owner", return_value=(123, 456)
        ):
            self.assertEqual(
                (123, 456),
                acceptance._expected_worker_file_owner(
                    acceptance.SEARCH_WORKER_USER,
                    acceptance.SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY,
                    acceptance.SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY,
                    acceptance.SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY,
                ),
            )
        for key, value in (
            (acceptance.SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY, "0"),
            (acceptance.SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY, "01"),
            (acceptance.SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY, "0600"),
        ):
            rejected = dict(values)
            rejected[key] = value
            with self.subTest(key=key, value=value), patch.dict(
                os.environ, rejected, clear=False
            ), patch.object(
                acceptance, "_worker_file_owner", return_value=(123, 456)
            ), self.assertRaises(acceptance.PilotAcceptanceError):
                acceptance._expected_worker_file_owner(
                    acceptance.SEARCH_WORKER_USER,
                    acceptance.SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY,
                    acceptance.SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY,
                    acceptance.SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY,
                )
        with patch.dict(os.environ, values, clear=False), patch.object(
            acceptance, "_worker_file_owner", return_value=(123, 999)
        ), self.assertRaises(acceptance.PilotAcceptanceError):
            acceptance._expected_worker_file_owner(
                acceptance.SEARCH_WORKER_USER,
                acceptance.SEARCH_HEARTBEAT_UID_ENVIRONMENT_KEY,
                acceptance.SEARCH_HEARTBEAT_GID_ENVIRONMENT_KEY,
                acceptance.SEARCH_HEARTBEAT_MODE_ENVIRONMENT_KEY,
            )

    def test_operator_boundary_loads_only_the_fixed_staging_runtime_mode(self) -> None:
        owner = "user_owner000"
        provider = {
            key: f"provider-{index}"
            for index, key in enumerate(acceptance.PROVIDER_KEYS)
        }
        objects = {
            key: f"object-{index}"
            for index, key in enumerate(acceptance.OBJECT_KEYS)
        }
        with patch.object(
            acceptance,
            "read_protected_environment",
            side_effect=(
                {
                    acceptance.OWNER_ID_KEY: owner,
                    acceptance.RUNTIME_ENVIRONMENT_KEY: "staging",
                },
                {acceptance.MIGRATION_URL_KEY: "migration-url"},
                {acceptance.RUNTIME_URL_KEY: "runtime-url"},
                provider,
                objects,
            ),
        ) as reader:
            loaded = acceptance._load_operator_boundary()
        self.assertEqual(owner, loaded[0])
        self.assertEqual("staging", loaded[4][acceptance.RUNTIME_ENVIRONMENT_KEY])
        self.assertEqual(
            (acceptance.OWNER_ID_KEY, acceptance.RUNTIME_ENVIRONMENT_KEY),
            reader.call_args_list[0].args[1],
        )

    def test_operator_boundary_rejects_a_non_staging_protected_runtime_mode(self) -> None:
        with patch.object(
            acceptance,
            "read_protected_environment",
            return_value={
                acceptance.OWNER_ID_KEY: "user_owner000",
                acceptance.RUNTIME_ENVIRONMENT_KEY: "local",
            },
        ), self.assertRaises(acceptance.PilotAcceptanceError):
            acceptance._load_operator_boundary()

    def test_operator_runtime_boundary_requires_staging_before_backend_validation(self) -> None:
        provider = {"PROVIDER_TEST_VALUE": "provider-value"}
        objects = {
            "OBJECT_TEST_VALUE": "object-value",
            acceptance.RUNTIME_ENVIRONMENT_KEY: "local",
        }
        with (
            patch("provider_secrets._parse_keyring") as provider_keyring,
            patch("object_store.parse_object_keyring") as object_keyring,
            patch("object_store.runtime_object_backend") as object_backend,
            self.assertRaises(acceptance.PilotAcceptanceError),
        ):
            acceptance._validate_operator_runtime_boundary(provider, objects)
        provider_keyring.assert_not_called()
        object_keyring.assert_not_called()
        object_backend.assert_not_called()

    def test_operator_runtime_boundary_validates_crypto_and_constructs_backend_offline(self) -> None:
        provider = {"PROVIDER_TEST_VALUE": "provider-value"}
        objects = {
            "OBJECT_TEST_VALUE": "object-value",
            acceptance.RUNTIME_ENVIRONMENT_KEY: "staging",
        }
        backend = object_store.R2ObjectBackend(
            object_store.R2ObjectStoreConfiguration(
                endpoint="https://synthetic.r2.cloudflarestorage.com",
                bucket="synthetic-bucket",
                access_key_id="synthetic-access-key",
                secret_access_key="synthetic-secret-key-value-00000000",
            )
        )

        def observe_provider_environment():
            self.assertEqual("provider-value", os.environ["PROVIDER_TEST_VALUE"])
            self.assertEqual("object-value", os.environ["OBJECT_TEST_VALUE"])

        with (
            patch(
                "provider_secrets._parse_keyring",
                side_effect=observe_provider_environment,
            ) as provider_keyring,
            patch("object_store.parse_object_keyring") as object_keyring,
            patch(
                "object_store.runtime_object_backend", return_value=backend
            ) as object_backend,
            patch.object(
                object_store.R2ObjectBackend,
                "_client",
                side_effect=AssertionError("network client must stay lazy"),
            ) as object_client,
        ):
            acceptance._validate_operator_runtime_boundary(provider, objects)
        provider_keyring.assert_called_once_with()
        object_keyring.assert_called_once_with()
        object_backend.assert_called_once_with()
        object_client.assert_not_called()
        self.assertNotIn("PROVIDER_TEST_VALUE", os.environ)
        self.assertNotIn("OBJECT_TEST_VALUE", os.environ)

    def test_operator_runtime_boundary_exercises_real_factory_without_creating_client(self) -> None:
        key_id = "synthetic-key"
        keyring = self.encoded_keyring(key_id)
        provider = {
            "CAREERTRELLIS_PROVIDER_SECRET_STORE": "postgres_encrypted",
            "CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID": key_id,
            "CAREERTRELLIS_PROVIDER_SECRET_KEYRING_B64": keyring,
        }
        objects = {
            acceptance.RUNTIME_ENVIRONMENT_KEY: "staging",
            "CAREERTRELLIS_OBJECT_STORE": "r2_encrypted",
            "CAREERTRELLIS_OBJECT_STORE_ENDPOINT": (
                f"https://{'a' * 32}.r2.cloudflarestorage.com"
            ),
            "CAREERTRELLIS_OBJECT_STORE_BUCKET": "synthetic-bucket",
            "CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID": "synthetic-access-key",
            "CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY": (
                "synthetic-secret-key-value-00000000"
            ),
            "CAREERTRELLIS_OBJECT_STORE_ACTIVE_KEY_ID": key_id,
            "CAREERTRELLIS_OBJECT_STORE_KEYRING_B64": keyring,
        }
        with patch(
            "boto3.client",
            side_effect=AssertionError("network client must stay lazy"),
        ) as client:
            acceptance._validate_operator_runtime_boundary(provider, objects)
        client.assert_not_called()

    def test_operator_runtime_boundary_maps_backend_details_to_fixed_error(self) -> None:
        with (
            patch(
                "provider_secrets._parse_keyring",
                side_effect=RuntimeError("sensitive provider detail"),
            ),
            self.assertRaisesRegex(
                acceptance.PilotAcceptanceError,
                re.escape(acceptance.FIXED_ERROR),
            ) as raised,
        ):
            acceptance._validate_operator_runtime_boundary(
                {}, {acceptance.RUNTIME_ENVIRONMENT_KEY: "staging"}
            )
        self.assertNotIn("sensitive", str(raised.exception))

    def test_operator_runtime_boundary_restores_existing_environment_on_failure(self) -> None:
        provider_key = "PROVIDER_TEST_VALUE"
        object_key = "OBJECT_TEST_VALUE"
        original = {
            provider_key: "original-provider",
            object_key: "original-object",
            acceptance.RUNTIME_ENVIRONMENT_KEY: "original-mode",
        }
        provider = {provider_key: "temporary-provider"}
        objects = {
            object_key: "temporary-object",
            acceptance.RUNTIME_ENVIRONMENT_KEY: "staging",
        }
        with patch.dict(os.environ, original, clear=False):
            with (
                patch(
                    "provider_secrets._parse_keyring",
                    side_effect=RuntimeError("sensitive provider detail"),
                ),
                self.assertRaises(acceptance.PilotAcceptanceError),
            ):
                acceptance._validate_operator_runtime_boundary(provider, objects)
            self.assertEqual(original[provider_key], os.environ[provider_key])
            self.assertEqual(original[object_key], os.environ[object_key])
            self.assertEqual(
                original[acceptance.RUNTIME_ENVIRONMENT_KEY],
                os.environ[acceptance.RUNTIME_ENVIRONMENT_KEY],
            )

    def test_inspect_validates_operator_runtime_boundary_before_state_read(self) -> None:
        store = Mock()
        store.exists.return_value = False
        migration_engine = Mock()
        runtime_engine = Mock()
        provider = {"PROVIDER_TEST_VALUE": "provider-value"}
        objects = {
            "OBJECT_TEST_VALUE": "object-value",
            acceptance.RUNTIME_ENVIRONMENT_KEY: "staging",
        }
        with (
            patch.object(acceptance, "_release_binding", return_value=(RELEASE, BUILD)),
            patch.object(
                acceptance,
                "_load_operator_boundary",
                return_value=(
                    "user_owner000", "migration-url", "runtime-url",
                    provider, objects,
                ),
            ),
            patch.object(acceptance, "_validate_operator_runtime_boundary") as validate,
            patch.object(
                acceptance,
                "_create_engine",
                side_effect=(migration_engine, runtime_engine),
            ),
        ):
            result = acceptance._run_operation("inspect", store)
        validate.assert_called_once_with(provider, objects)
        store.exists.assert_called_once_with()
        self.assertEqual(acceptance.OperationResult("inspect", "absent", "none"), result)
        migration_engine.dispose.assert_called_once_with()
        runtime_engine.dispose.assert_called_once_with()

    def test_inspect_offline_failure_never_reads_state(self) -> None:
        store = Mock()
        migration_engine = Mock()
        runtime_engine = Mock()
        provider = {"PROVIDER_TEST_VALUE": "provider-value"}
        objects = {acceptance.RUNTIME_ENVIRONMENT_KEY: "staging"}
        with (
            patch.object(acceptance, "_release_binding", return_value=(RELEASE, BUILD)),
            patch.object(
                acceptance,
                "_load_operator_boundary",
                return_value=(
                    "user_owner000", "migration-url", "runtime-url",
                    provider, objects,
                ),
            ),
            patch.object(
                acceptance,
                "_validate_operator_runtime_boundary",
                side_effect=acceptance.PilotAcceptanceError(acceptance.FIXED_ERROR),
            ),
            patch.object(
                acceptance,
                "_create_engine",
                side_effect=(migration_engine, runtime_engine),
            ),
            self.assertRaises(acceptance.PilotAcceptanceError),
        ):
            acceptance._run_operation("inspect", store)
        store.exists.assert_not_called()
        store.read.assert_not_called()
        migration_engine.dispose.assert_called_once_with()
        runtime_engine.dispose.assert_called_once_with()

    def test_prepare_fails_offline_before_identity_prompt_or_state_write(self) -> None:
        store = Mock()
        store.exists.return_value = False
        migration_engine = Mock()
        runtime_engine = Mock()
        provider = {"PROVIDER_TEST_VALUE": "provider-value"}
        objects = {acceptance.RUNTIME_ENVIRONMENT_KEY: "staging"}
        with (
            patch.object(acceptance, "_release_binding", return_value=(RELEASE, BUILD)),
            patch.object(
                acceptance,
                "_load_operator_boundary",
                return_value=(
                    "user_owner000", "migration-url", "runtime-url",
                    provider, objects,
                ),
            ),
            patch.object(
                acceptance,
                "_validate_operator_runtime_boundary",
                side_effect=acceptance.PilotAcceptanceError(acceptance.FIXED_ERROR),
            ),
            patch.object(acceptance, "_interactive_identity") as identity_prompt,
            patch.object(acceptance, "mapping_preflight") as mapping_preflight,
            patch.object(
                acceptance,
                "_create_engine",
                side_effect=(migration_engine, runtime_engine),
            ),
            self.assertRaises(acceptance.PilotAcceptanceError),
        ):
            acceptance._run_operation("prepare", store)
        identity_prompt.assert_not_called()
        mapping_preflight.assert_not_called()
        store.write.assert_not_called()
        migration_engine.dispose.assert_called_once_with()
        runtime_engine.dispose.assert_called_once_with()


class ReceiptBoundaryTests(unittest.TestCase):
    def prepared_state(self) -> acceptance.AcceptanceState:
        fixture = acceptance.FixtureIds(*range(1, 12))
        return acceptance.AcceptanceState.new(
            release=RELEASE,
            build=BUILD,
            owner_user_id=41,
            pilot_user_id=42,
            provider="gemini",
        ).transition("prepared", fixture=fixture.__dict__)

    @staticmethod
    def mapping_evidence() -> dict[str, bool]:
        return {
            "disabled_verified": True,
            "recovered_verified": True,
        }

    def test_blocked_live_surfaces_prevent_success_receipt(self) -> None:
        checks = {name: True for name in acceptance.AUTOMATED_CHECKS}
        checks["provider_network_called"] = False
        checks["object_storage_live"] = "blocked"
        checks["worker_live"] = "blocked"
        state = self.prepared_state().transition(
            "automated_partial",
            automated_checks=checks,
            mapping_evidence=self.mapping_evidence(),
        )
        writer = Mock()
        with patch.object(acceptance, "write_receipt", writer):
            updated, written = acceptance.record_browser_checks(
                state, {name: True for name in acceptance.BROWSER_CHECKS}
            )
        self.assertEqual("browser_recorded", updated.phase)
        self.assertFalse(written)
        writer.assert_not_called()

    def test_complete_sanitized_checks_are_not_receipted_before_cleanup(self) -> None:
        checks = {name: True for name in acceptance.AUTOMATED_CHECKS}
        checks["provider_network_called"] = False
        state = self.prepared_state().transition(
            "automated_partial",
            automated_checks=checks,
            mapping_evidence=self.mapping_evidence(),
        )
        writer = Mock()
        with patch.object(acceptance, "write_receipt", writer):
            updated, written = acceptance.record_browser_checks(
                state, {name: True for name in acceptance.BROWSER_CHECKS}
            )
        self.assertFalse(written)
        writer.assert_not_called()
        self.assertIsNone(acceptance._receipt_document(updated))
        document = acceptance._receipt_document(updated, cleanup_complete=True)
        self.assertIsNotNone(document)
        self.assertEqual("two_user_acceptance", document["operation"])
        encoded = json.dumps(document, sort_keys=True)
        for forbidden in ("@", "cookie", "token", "object_key", "database_url"):
            self.assertNotIn(forbidden, encoded.lower())
        self.assertIsNone(
            re.search(r"(?<![A-Za-z0-9_])user_[A-Za-z0-9_]{6,120}", encoded)
        )
        unsupported_claims = {
            "email_verification_required",
            "password_login_allowed",
            "mfa_required",
            "owner_immutable",
            "search_queue_isolated",
            "erasure_isolated",
            "disabled_work_contained",
            "worker_isolated",
        }
        self.assertTrue(unsupported_claims.isdisjoint(document["checks"]))


class VerificationHelperTests(unittest.TestCase):
    @staticmethod
    def fixture() -> acceptance.FixtureIds:
        return acceptance.FixtureIds(*range(1, 12))

    @staticmethod
    def state() -> acceptance.AcceptanceState:
        return acceptance.AcceptanceState.new(
            release=RELEASE,
            build=BUILD,
            owner_user_id=41,
            pilot_user_id=42,
            provider="gemini",
        )

    def test_export_checks_cover_every_populated_fixture_collection(self) -> None:
        fixture = self.fixture()
        owner = {name: [] for name in acceptance.EXPORT_FIELDS}
        owner.update({
            "profile": [{"id": 900}],
            "base_resumes": [{"id": fixture.owner_resume_id}],
            "base_resume_versions": [{"id": fixture.owner_resume_version_id}],
            "jobs": [{"id": fixture.owner_job_id}],
            "applications": [{"id": fixture.owner_application_id}],
        })
        pilot = {name: [] for name in acceptance.EXPORT_FIELDS}
        pilot.update({
            "profile": [{"id": fixture.pilot_profile_id}],
            "base_resumes": [{"id": fixture.pilot_resume_id}],
            "base_resume_versions": [{"id": fixture.pilot_resume_version_id}],
            "jobs": [{"id": fixture.pilot_job_id}],
            "applications": [{"id": fixture.pilot_application_id}],
        })
        self.assertTrue(
            acceptance.exports_reciprocally_isolated(owner, pilot, fixture)
        )
        cross_ids = {
            "profile": fixture.pilot_profile_id,
            "base_resumes": fixture.pilot_resume_id,
            "base_resume_versions": fixture.pilot_resume_version_id,
            "jobs": fixture.pilot_job_id,
            "applications": fixture.pilot_application_id,
        }
        for collection, cross_id in cross_ids.items():
            with self.subTest(collection=collection):
                contaminated = {
                    name: [dict(row) for row in rows]
                    for name, rows in owner.items()
                }
                contaminated[collection].append({"id": cross_id})
                self.assertFalse(
                    acceptance.exports_reciprocally_isolated(
                        contaminated, pilot, fixture
                    )
                )
        reverse_profile = {
            name: [dict(row) for row in rows]
            for name, rows in pilot.items()
        }
        reverse_profile["profile"].append({"id": 900})
        self.assertFalse(
            acceptance.exports_reciprocally_isolated(
                owner, reverse_profile, fixture
            )
        )
        owner_with_saved_search = {
            name: [dict(row) for row in rows]
            for name, rows in owner.items()
        }
        pilot_with_saved_search = {
            name: [dict(row) for row in rows]
            for name, rows in pilot.items()
        }
        owner_with_saved_search["saved_searches"].append({"id": 901})
        pilot_with_saved_search["saved_searches"].append({"id": 901})
        self.assertFalse(
            acceptance.exports_reciprocally_isolated(
                owner_with_saved_search, pilot_with_saved_search, fixture
            )
        )

    def test_provider_namespace_is_allowed_absent_and_deterministic(self) -> None:
        rows = [
            {"owner_user_id": 41, "provider": "gemini"},
            {"owner_user_id": 42, "provider": "openai"},
        ]
        self.assertEqual(
            "google_maps",
            acceptance._choose_provider_namespace(rows, 41, 42),
        )
        occupied = [
            {"owner_user_id": 41, "provider": provider}
            for provider in acceptance.PROVIDER_NAMES
        ]
        with self.assertRaises(acceptance.PilotAcceptanceError):
            acceptance._choose_provider_namespace(occupied, 41, 42)

    def test_provider_namespace_recheck_takes_table_lock_and_fails_closed(self) -> None:
        connection = Mock()
        connection.scalar.side_effect = [1, 0]
        with self.assertRaises(acceptance.PilotAcceptanceError):
            acceptance._assert_provider_namespace_free(
                connection, self.state()
            )
        self.assertIn(
            "LOCK TABLE careertrellis.provider_secrets",
            connection.exec_driver_sql.call_args.args[0],
        )
        self.assertEqual(2, connection.scalar.call_count)
        self.assertEqual(
            ["41", "42"],
            [
                call.args[1]["user_id"]
                for call in connection.execute.call_args_list
            ],
        )

    def test_provider_namespace_selection_sets_each_rls_tenant_context(self) -> None:
        context = MagicMock()
        connection = Mock()
        owner_rows = Mock()
        owner_rows.mappings.return_value.all.return_value = [{
            "owner_user_id": 41,
            "provider": "gemini",
        }]
        pilot_rows = Mock()
        pilot_rows.mappings.return_value.all.return_value = [{
            "owner_user_id": 42,
            "provider": "openai",
        }]
        connection.execute.side_effect = [Mock(), owner_rows, Mock(), pilot_rows]
        with patch.object(
            acceptance,
            "_protected_migration_transaction",
            return_value=(context, connection),
        ):
            self.assertEqual(
                "google_maps",
                acceptance.select_provider_namespace(Mock(), 41, 42),
            )
        self.assertEqual(
            ["41", "42"],
            [
                connection.execute.call_args_list[index].args[1]["user_id"]
                for index in (0, 2)
            ],
        )
        for index, owner_user_id in ((1, 41), (3, 42)):
            self.assertEqual(
                owner_user_id,
                connection.execute.call_args_list[index].args[1]["owner"],
            )

    def test_object_upload_and_cleanup_use_predeclared_exact_keys(self) -> None:
        keyring = object_store.ObjectKeyring(
            active_key_id="acceptance-test",
            keys={"acceptance-test": b"k" * 32},
        )
        draft = acceptance.AcceptanceState.new(
            release=RELEASE, build=BUILD, owner_user_id=41, pilot_user_id=42,
            provider="gemini",
        )
        owner = object_store.encrypt_object(
            41, f"{draft.marker}-owner-object".encode("ascii"), "text/plain",
            keyring=keyring,
        )
        pilot = object_store.encrypt_object(
            42, f"{draft.marker}-pilot-object".encode("ascii"), "text/plain",
            keyring=keyring,
        )
        document = draft.document()
        document["owner_object_id"] = str(owner.metadata.id)
        document["pilot_object_id"] = str(pilot.metadata.id)
        state = acceptance.AcceptanceState.from_mapping(document)
        backend = object_store.InMemoryObjectBackend()
        acceptance.upload_staged_objects(
            backend, state, owner, pilot, object_keyring=keyring
        )
        self.assertIsNotNone(backend.head(owner.metadata.object_key))
        self.assertIsNotNone(backend.head(pilot.metadata.object_key))
        self.assertTrue(acceptance.cleanup_objects(backend, state))
        self.assertIsNone(backend.head(owner.metadata.object_key))
        self.assertIsNone(backend.head(pilot.metadata.object_key))

    def test_worker_heartbeat_requires_exact_build_schema_and_freshness(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.json"
            valid = {
                "build": BUILD,
                "pid": 123,
                "status": "idle",
                "updated_at": now.isoformat(),
            }
            path.write_text(json.dumps(valid), encoding="utf-8")
            path.chmod(0o600)
            owner = (11, 12)
            metadata = Mock(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=owner[0],
                st_gid=owner[1],
            )
            with (
                patch.object(Path, "lstat", return_value=metadata),
                patch.object(Path, "is_symlink", return_value=False),
            ):
                self.assertTrue(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, owner, now=now
                ))
                valid["status"] = "persistence_unavailable"
                path.write_text(json.dumps(valid), encoding="utf-8")
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, owner, now=now
                ))
                valid["status"] = "idle"
                valid["secret"] = "forbidden"
                path.write_text(json.dumps(valid), encoding="utf-8")
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, owner, now=now
                ))
                valid.pop("secret")
                valid["updated_at"] = (now - timedelta(minutes=3)).isoformat()
                path.write_text(json.dumps(valid), encoding="utf-8")
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, owner, now=now
                ))

    def test_worker_heartbeat_binds_pid_owner_mode_and_restart(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.json"
            payload = {
                "build": BUILD,
                "pid": 123,
                "status": "idle",
                "updated_at": now.isoformat(),
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)
            owner = (11, 12)
            safe_metadata = Mock(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=owner[0],
                st_gid=owner[1],
            )
            with (
                patch.object(Path, "lstat", return_value=safe_metadata),
                patch.object(Path, "is_symlink", return_value=False),
            ):
                self.assertTrue(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, owner, now=now
                ))
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 124, owner, now=now
                ))
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, (owner[0] + 1, owner[1]), now=now
                ))
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 123, (owner[0], owner[1] + 1), now=now
                ))
                payload["pid"] = 124
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertTrue(acceptance.validate_worker_heartbeat(
                    path, BUILD, 124, owner, now=now
                ))
            permissive_metadata = Mock(
                st_mode=stat.S_IFREG | 0o640,
                st_uid=owner[0],
                st_gid=owner[1],
            )
            with (
                patch.object(Path, "lstat", return_value=permissive_metadata),
                patch.object(Path, "is_symlink", return_value=False),
            ):
                self.assertFalse(acceptance.validate_worker_heartbeat(
                    path, BUILD, 124, owner, now=now
                ))

    def test_cleanup_locks_rows_and_binds_inspected_values(self) -> None:
        connection = Mock()
        selected = Mock()
        selected.scalar_one_or_none.return_value = "fixture-marker-value"
        deleted = Mock(rowcount=1)
        connection.execute.side_effect = [selected, deleted]
        acceptance._delete_exact(
            connection,
            "jobs",
            7,
            41,
            "title",
            "fixture-marker",
        )
        select_statement = str(connection.execute.call_args_list[0].args[0])
        delete_statement = str(connection.execute.call_args_list[1].args[0])
        delete_parameters = connection.execute.call_args_list[1].args[1]
        self.assertIn("FOR UPDATE", select_statement)
        self.assertIn("title = :selected_value", delete_statement)
        self.assertEqual("fixture-marker-value", delete_parameters["selected_value"])

    def test_provider_cleanup_locks_decrypts_marker_and_value_binds_delete(self) -> None:
        state = self.state()
        connection = Mock()
        selected = Mock()
        selected.mappings.return_value.all.return_value = [{
            "id": 17,
            "key_id": "synthetic-key",
            "ciphertext": b"synthetic-ciphertext",
            "revoked_at": None,
        }]
        deleted = Mock(rowcount=1)
        connection.execute.side_effect = [Mock(), selected, deleted]
        with patch(
            "provider_secrets.decrypt_provider_secret",
            return_value=f"{state.marker}-owner-secret",
        ):
            acceptance._delete_provider_secret(
                connection,
                state,
                Mock(),
                17,
                state.owner_user_id,
                f"{state.marker}-owner-secret",
            )
        select_statement = str(connection.execute.call_args_list[1].args[0])
        delete_statement = str(connection.execute.call_args_list[2].args[0])
        delete_parameters = connection.execute.call_args_list[2].args[1]
        self.assertIn("FOR UPDATE", select_statement)
        self.assertIn("ciphertext=:ciphertext", delete_statement)
        self.assertEqual(
            b"synthetic-ciphertext", delete_parameters["ciphertext"]
        )

    def test_crash_cleanup_preserves_unrelated_real_provider_secret(self) -> None:
        state = self.state()
        connection = Mock()
        selected = Mock()
        selected.mappings.return_value.all.return_value = [{
            "id": 17,
            "key_id": "real-key",
            "ciphertext": b"real-ciphertext",
            "revoked_at": None,
        }]
        connection.execute.side_effect = [Mock(), selected]
        with patch(
            "provider_secrets.decrypt_provider_secret",
            return_value="unrelated-real-secret",
        ):
            acceptance._delete_provider_secret(
                connection,
                state,
                Mock(),
                None,
                state.owner_user_id,
                f"{state.marker}-owner-secret",
            )
        self.assertEqual(2, connection.execute.call_count)

    def test_crash_cleanup_deletes_matching_synthetic_provider_secret(self) -> None:
        state = self.state()
        connection = Mock()
        selected = Mock()
        selected.mappings.return_value.all.return_value = [{
            "id": 17,
            "key_id": "synthetic-key",
            "ciphertext": b"synthetic-ciphertext",
            "revoked_at": None,
        }]
        deleted = Mock(rowcount=1)
        connection.execute.side_effect = [Mock(), selected, deleted]
        with patch(
            "provider_secrets.decrypt_provider_secret",
            return_value=f"{state.marker}-owner-secret",
        ):
            acceptance._delete_provider_secret(
                connection,
                state,
                Mock(),
                None,
                state.owner_user_id,
                f"{state.marker}-owner-secret",
            )
        self.assertEqual(3, connection.execute.call_count)

    def test_marker_search_does_not_stringify_unknown_objects(self) -> None:
        marker = "pilot-acceptance-" + "b" * 32
        self.assertTrue(acceptance._contains_marker({"rows": [{"name": marker}]}, marker))
        self.assertFalse(acceptance._contains_marker({"rows": [{"id": 42}]}, marker))
        class MustNotStringify:
            def __str__(self) -> str:
                raise AssertionError("Unknown objects must not be stringified.")

        self.assertFalse(acceptance._contains_marker(MustNotStringify(), marker))

    def test_fixed_output_never_contains_state_identifiers(self) -> None:
        stream = io.StringIO()
        with patch("sys.stdout", stream):
            acceptance._fixed_result(
                acceptance.OperationResult(
                    "verify",
                    "blocked",
                    "automated_partial",
                    {
                        "mapping_boundary": True,
                        "object_storage_live": "blocked",
                        "provider_network_called": False,
                    },
                )
            )
        output = stream.getvalue()
        self.assertIn("identity_disclosed=no", output)
        self.assertIn("object_storage_live=blocked", output)
        self.assertNotIn("user_", output)
        self.assertNotIn("@", output)


class MainBoundaryTests(unittest.TestCase):
    def test_identity_prompt_hides_terminal_before_accepting_prompt_timed_input(self) -> None:
        events: list[str] = []
        identity = "user_01PILOTBOUNDARY"

        class PromptTimedInput:
            pending = ""

            @staticmethod
            def isatty() -> bool:
                return True

            @staticmethod
            def fileno() -> int:
                return 42

            def readline(self, limit: int) -> str:
                if limit != 257:
                    raise AssertionError("unexpected identity input bound")
                events.append("read")
                line, separator, remainder = self.pending.partition("\n")
                self.pending = remainder
                return f"{line}{separator}"

        class PromptStream:
            @staticmethod
            def isatty() -> bool:
                return True

            def write(self, value: str) -> int:
                if "Immutable WorkOS user ID:" in value:
                    events.append("prompt")
                    terminal_input.pending = (
                        f"{identity}\nsynthetic-surplus-input\n"
                    )
                return len(value)

            @staticmethod
            def flush() -> None:
                return None

        terminal_input = PromptTimedInput()
        terminal_error = PromptStream()
        echo_flag = 8
        flush_action = 1
        original_attributes = [0, 0, 0, echo_flag, 0, 0, []]

        def set_terminal_attributes(
            _file_descriptor: int, action: int, attributes
        ) -> None:
            self.assertEqual(flush_action, action)
            if attributes[3] & echo_flag:
                events.append("restore")
                terminal_input.pending = ""
            else:
                events.append("hide")
                self.assertEqual(0, attributes[3] & echo_flag)
                terminal_input.pending = ""

        fake_termios = type(
            "FakeTermios",
            (),
            {
                "ECHO": echo_flag,
                "TCSAFLUSH": flush_action,
                "tcgetattr": staticmethod(lambda _file_descriptor: original_attributes),
                "tcsetattr": staticmethod(set_terminal_attributes),
            },
        )
        with (
            patch.object(sys, "stdin", terminal_input),
            patch.object(sys, "stderr", terminal_error),
            patch.dict(sys.modules, {"termios": fake_termios}),
        ):
            value = acceptance._interactive_identity()

        self.assertEqual(identity, value)
        self.assertEqual(["hide", "prompt", "read", "restore"], events)
        self.assertEqual("", terminal_input.pending)
        self.assertEqual(echo_flag, original_attributes[3] & echo_flag)

    def test_main_requires_root_tty_and_one_fixed_operation(self) -> None:
        for arguments in ([], ["unknown"], ["verify-active", "extra"]):
            with self.subTest(arguments=arguments), patch.object(acceptance, "_effective_uid", return_value=0), patch(
                "sys.stderr", io.StringIO()
            ):
                self.assertEqual(1, acceptance.main(arguments))
        with patch.object(acceptance, "_effective_uid", return_value=1), patch("sys.stderr", io.StringIO()):
            self.assertEqual(1, acceptance.main(["inspect"]))

    def test_main_prints_only_sanitized_result(self) -> None:
        stdin = Mock()
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin.isatty.return_value = True
        stdout.isatty = Mock(return_value=True)
        stderr.isatty = Mock(return_value=True)
        result = acceptance.OperationResult("inspect", "ok", "prepared")
        with (
            patch.object(acceptance, "_effective_uid", return_value=0),
            patch("sys.stdin", stdin),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
            patch.object(acceptance, "_run_operation", return_value=result),
        ):
            self.assertEqual(0, acceptance.main(["inspect"]))
        self.assertEqual("", stderr.getvalue())
        self.assertIn("operation=inspect", stdout.getvalue())
        self.assertIn("identity_disclosed=no", stdout.getvalue())

    def test_main_never_prints_nested_exception_details(self) -> None:
        stdin = Mock()
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin.isatty.return_value = True
        stdout.isatty = Mock(return_value=True)
        stderr.isatty = Mock(return_value=True)
        with (
            patch.object(acceptance, "_effective_uid", return_value=0),
            patch("sys.stdin", stdin),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
            patch.object(
                acceptance,
                "_run_operation",
                side_effect=RuntimeError("sensitive nested detail"),
            ),
        ):
            self.assertEqual(1, acceptance.main(["inspect"]))
        self.assertEqual("", stdout.getvalue())
        self.assertEqual(f"{acceptance.FIXED_ERROR}\n", stderr.getvalue())
        self.assertNotIn("sensitive", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
