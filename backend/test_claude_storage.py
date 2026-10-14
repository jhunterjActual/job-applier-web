"""Synthetic coverage for Claude credentials in the encrypted tenant store."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

import provider_secrets


class ClaudeEncryptedStorageTests(unittest.TestCase):
    def setUp(self):
        self.keyring = provider_secrets.ProviderSecretKeyring(
            active_key_id="synthetic-current",
            keys={"synthetic-current": bytes(range(32))},
        )
        self.connection = MagicMock()
        self.engine = object()
        self.keyring_patch = patch.object(
            provider_secrets, "_parse_keyring", return_value=self.keyring
        )
        self.engine_patch = patch.object(
            provider_secrets, "runtime_postgres_engine", return_value=self.engine
        )
        self.transaction_patch = patch.object(provider_secrets, "tenant_transaction")
        self.keyring_patch.start()
        self.engine_patch.start()
        self.transaction = self.transaction_patch.start()
        self.transaction.return_value.__enter__.return_value = self.connection
        self.addCleanup(self.keyring_patch.stop)
        self.addCleanup(self.engine_patch.stop)
        self.addCleanup(self.transaction_patch.stop)

    def encrypt(self):
        return provider_secrets.encrypt_provider_secret(
            17, "anthropic", "synthetic-claude-key", keyring=self.keyring
        )

    def test_ciphertext_rejects_other_owner_provider_and_tampering(self):
        key_id, ciphertext = self.encrypt()
        self.assertEqual(
            "synthetic-claude-key",
            provider_secrets.decrypt_provider_secret(17, "anthropic", key_id, ciphertext),
        )
        self.assertNotEqual(ciphertext, self.encrypt()[1])
        self.assertNotIn(b"synthetic-claude-key", ciphertext)
        for owner, provider, raw in (
            (18, "anthropic", ciphertext),
            (17, "openai", ciphertext),
            (17, "anthropic", ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])),
        ):
            with self.subTest(owner=owner, provider=provider):
                with self.assertRaises(provider_secrets.ProviderSecretIntegrityError):
                    provider_secrets.decrypt_provider_secret(owner, provider, key_id, raw)

    def test_save_sends_only_ciphertext_in_owner_transaction(self):
        provider_secrets.save_provider_secret(17, "anthropic", "synthetic-claude-key")
        self.transaction.assert_called_once_with(self.engine, 17)
        statement, values = self.connection.execute.call_args.args
        self.assertIn("ON CONFLICT (owner_user_id, provider)", str(statement))
        self.assertEqual(17, values["owner_user_id"])
        self.assertEqual("anthropic", values["provider"])
        self.assertFalse(values["revoked"])
        self.assertNotIn("synthetic-claude-key", str(values))
        self.assertEqual(
            "synthetic-claude-key",
            provider_secrets.decrypt_provider_secret(
                17, "anthropic", values["key_id"], values["ciphertext"]
            ),
        )

    def test_load_filters_owner_provider_and_revocation(self):
        key_id, ciphertext = self.encrypt()
        result = self.connection.execute.return_value.mappings.return_value
        result.one_or_none.return_value = {"key_id": key_id, "ciphertext": ciphertext}
        with provider_secrets.owner_user_scope(17):
            self.assertEqual(
                "synthetic-claude-key", provider_secrets.current_provider_secret("anthropic")
            )
        self.transaction.assert_called_once_with(self.engine, 17)
        statement, values = self.connection.execute.call_args.args
        self.assertIn("owner_user_id = :owner_user_id", str(statement))
        self.assertIn("provider = :provider", str(statement))
        self.assertIn("revoked_at IS NULL", str(statement))
        self.assertEqual({"owner_user_id": 17, "provider": "anthropic"}, values)
        result.one_or_none.return_value = None
        self.assertFalse(provider_secrets.provider_secret_configured(17, "anthropic"))

    def test_revocation_replaces_ciphertext_with_unusable_value(self):
        provider_secrets.revoke_provider_secret(17, "anthropic")
        self.transaction.assert_called_once_with(self.engine, 17)
        values = self.connection.execute.call_args.args[1]
        self.assertTrue(values["revoked"])
        self.assertEqual("anthropic", values["provider"])
        with self.assertRaises(provider_secrets.ProviderSecretIntegrityError):
            provider_secrets.decrypt_provider_secret(
                17, "anthropic", values["key_id"], values["ciphertext"]
            )

    def test_rotation_reencrypts_anthropic_without_changing_owner_or_value(self):
        key_id, ciphertext = self.encrypt()
        self.connection.execute.return_value.mappings.return_value.all.return_value = [
            {"provider": "anthropic", "key_id": key_id, "ciphertext": ciphertext}
        ]
        self.assertEqual(1, provider_secrets.rotate_provider_secrets(17))
        self.transaction.assert_called_once_with(self.engine, 17)
        statement, values = self.connection.execute.call_args.args
        self.assertIn("owner_user_id = :owner_user_id AND provider = :provider", str(statement))
        self.assertEqual(17, values["owner_user_id"])
        self.assertEqual("anthropic", values["provider"])
        self.assertNotEqual(ciphertext, values["ciphertext"])
        self.assertEqual(
            "synthetic-claude-key",
            provider_secrets.decrypt_provider_secret(
                17, "anthropic", values["key_id"], values["ciphertext"]
            ),
        )

    def test_invalid_batch_is_rejected_before_any_write(self):
        with self.assertRaises(ValueError):
            provider_secrets.update_provider_secrets(
                17, {"anthropic": "synthetic-claude-key", "unknown": "synthetic-other-key"}
            )
        self.transaction.assert_not_called()
        self.connection.execute.assert_not_called()

    def test_existing_postgres_schema_needs_no_claude_plaintext_column(self):
        path = Path(__file__).parent / "migrations" / "versions" / "20260811_0001_tenant_rls_foundation.py"
        spec = importlib.util.spec_from_file_location("claude_schema_foundation", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with patch.object(migration, "op") as operations:
            migration.upgrade()
        tables = {call.args[0]: call.args[1:] for call in operations.create_table.call_args_list}
        for table, provider_column in (("profiles", "ai_provider"), ("provider_secrets", "provider")):
            columns = {item.name: item for item in tables[table] if isinstance(item, sa.Column)}
            self.assertIsInstance(columns[provider_column].type, sa.Text)
            self.assertFalse(any(isinstance(item, sa.CheckConstraint) for item in tables[table]))
            self.assertFalse(any(name.endswith("api_key") for name in columns))
            self.assertIn("owner_user_id", columns)
            statements = [str(call.args[0]) for call in operations.execute.call_args_list]
            self.assertIn(
                f'ALTER TABLE "careertrellis"."{table}" FORCE ROW LEVEL SECURITY', statements
            )
        secret_columns = {
            item.name: item for item in tables["provider_secrets"] if isinstance(item, sa.Column)
        }
        self.assertIsInstance(secret_columns["ciphertext"].type, sa.LargeBinary)


if __name__ == "__main__":
    unittest.main()
