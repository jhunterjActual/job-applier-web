"""Add irreversible account erasure acceptance and worker brokers.

Revision ID: 20260814_0009
Revises: 20260813_0008
Create Date: 2026-08-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260814_0009"
down_revision: Union[str, Sequence[str], None] = "20260813_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
RUNTIME_ROLE = "careertrellis_runtime"
SEARCH_WORKER_ROLE = "careertrellis_worker"
ERASURE_WORKER_ROLE = "careertrellis_erasure_worker"


def _revoke_function(signature: str) -> None:
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.{signature} FROM PUBLIC, "
        f"{RUNTIME_ROLE}, {SEARCH_WORKER_ROLE}, {ERASURE_WORKER_ROLE}"
    )


def upgrade() -> None:
    # This marker distinguishes an idempotently deleted R2 object from a blob
    # that is merely tombstoned for ordinary generated-material reconciliation.
    op.add_column(
        "stored_objects",
        sa.Column("erasure_deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # This is an internal operational receipt, not a user/bearer-token surface.
    # Completion removes the app_users row immediately and SET NULL preserves
    # only the minimized receipt for its exact 30-day retention window.
    op.create_table(
        "account_erasure_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey(f"{SCHEMA}.app_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="accepted"),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False, server_default="100"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('accepted','deleting_objects','needs_attention','completed')",
            name="ck_account_erasure_requests_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts BETWEEN 1 AND 100 AND attempts <= max_attempts",
            name="ck_account_erasure_requests_attempts",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('object_delete_failed','persistence_unavailable','attempts_exhausted')",
            name="ck_account_erasure_requests_error_code",
        ),
        sa.CheckConstraint(
            "((status = 'completed' AND owner_user_id IS NULL AND completed_at IS NOT NULL "
            "AND receipt_expires_at = completed_at + INTERVAL '30 days' "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND worker_id IS NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL "
            "AND receipt_expires_at IS NULL))",
            name="ck_account_erasure_requests_receipt",
        ),
        sa.UniqueConstraint("owner_user_id", name="uq_account_erasure_requests_owner"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_account_erasure_requests_receipt_expiry",
        "account_erasure_requests",
        ["receipt_expires_at", "id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'completed'"),
    )

    # The queue is intentionally non-RLS and contains only opaque IDs and a
    # timestamp. Login roles never receive direct table privileges.
    op.create_table(
        "account_erasure_claim_queue",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            [f"{SCHEMA}.account_erasure_requests.id"],
            ondelete="CASCADE",
            name="fk_account_erasure_claim_queue_request",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_account_erasure_claim_queue_available",
        "account_erasure_claim_queue",
        ["available_at", "request_id"],
        schema=SCHEMA,
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.sync_account_erasure_claim_queue()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF NEW.owner_user_id IS NOT NULL
               AND NEW.status IN ('accepted', 'deleting_objects') THEN
                INSERT INTO {SCHEMA}.account_erasure_claim_queue (
                    request_id, owner_user_id, available_at
                ) VALUES (
                    NEW.id,
                    NEW.owner_user_id,
                    CASE WHEN NEW.status = 'deleting_objects'
                         THEN COALESCE(NEW.lease_expires_at, CURRENT_TIMESTAMP)
                         ELSE NEW.available_at END
                )
                ON CONFLICT (request_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    available_at = EXCLUDED.available_at;
            ELSE
                DELETE FROM {SCHEMA}.account_erasure_claim_queue
                WHERE request_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""CREATE TRIGGER account_erasure_claim_queue_sync
        AFTER INSERT OR UPDATE OF owner_user_id, status, available_at, lease_expires_at
        ON {SCHEMA}.account_erasure_requests
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.sync_account_erasure_claim_queue()"""
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.request_account_erasure()
        RETURNS TABLE (status varchar(24), accepted_at timestamptz)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            selected_owner_id bigint;
            selected_workos_user_id text;
            selected_disabled_at timestamptz;
            existing_request {SCHEMA}.account_erasure_requests%ROWTYPE;
        BEGIN
            IF SESSION_USER <> '{RUNTIME_ROLE}' THEN
                RAISE EXCEPTION 'account erasure acceptance is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            selected_owner_id := {SCHEMA}.current_app_user_id();
            IF selected_owner_id IS NULL THEN
                RAISE EXCEPTION 'account erasure acceptance is unavailable'
                    USING ERRCODE = '42501';
            END IF;

            SELECT u.workos_user_id, u.disabled_at
                INTO selected_workos_user_id, selected_disabled_at
            FROM {SCHEMA}.app_users AS u
            WHERE u.id = selected_owner_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'account erasure acceptance is unavailable'
                    USING ERRCODE = '42501';
            END IF;

            SELECT request.* INTO existing_request
            FROM {SCHEMA}.account_erasure_requests AS request
            WHERE request.owner_user_id = selected_owner_id
            FOR UPDATE;
            IF FOUND THEN
                status := 'accepted';
                accepted_at := existing_request.accepted_at;
                RETURN NEXT;
                RETURN;
            END IF;
            IF selected_disabled_at IS NOT NULL THEN
                RAISE EXCEPTION 'account erasure acceptance is unavailable'
                    USING ERRCODE = '42501';
            END IF;

            -- The forced app_users policy requires the already-authorized
            -- WorkOS identity on writes. It remains inside this transaction
            -- and is never returned or copied into the receipt/broker.
            PERFORM pg_catalog.set_config(
                'careertrellis.workos_user_id', selected_workos_user_id, true
            );
            UPDATE {SCHEMA}.app_users AS u
            SET disabled_at = CURRENT_TIMESTAMP
            WHERE u.id = selected_owner_id
              AND u.disabled_at IS NULL;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'account erasure acceptance is unavailable'
                    USING ERRCODE = '40001';
            END IF;

            INSERT INTO {SCHEMA}.account_erasure_requests (owner_user_id)
            VALUES (selected_owner_id)
            RETURNING account_erasure_requests.accepted_at INTO accepted_at;
            status := 'accepted';
            RETURN NEXT;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.claim_next_account_erasure(
            p_worker_id uuid, p_lease_seconds integer
        )
        RETURNS TABLE (
            request_id uuid,
            owner_user_id bigint,
            lease_token uuid,
            lease_expires_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            candidate {SCHEMA}.account_erasure_claim_queue%ROWTYPE;
            request {SCHEMA}.account_erasure_requests%ROWTYPE;
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}' THEN
                RAISE EXCEPTION 'account erasure claim is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_worker_id IS NULL OR p_lease_seconds < 30 OR p_lease_seconds > 900 THEN
                RAISE EXCEPTION 'invalid account erasure claim'
                    USING ERRCODE = '22023';
            END IF;

            LOOP
                SELECT queue.* INTO candidate
                FROM {SCHEMA}.account_erasure_claim_queue AS queue
                WHERE queue.available_at <= CURRENT_TIMESTAMP
                ORDER BY queue.available_at, queue.request_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1;
                IF NOT FOUND THEN
                    RETURN;
                END IF;

                SELECT selected.* INTO request
                FROM {SCHEMA}.account_erasure_requests AS selected
                WHERE selected.id = candidate.request_id
                  AND selected.owner_user_id = candidate.owner_user_id
                FOR UPDATE;
                IF NOT FOUND OR request.status NOT IN ('accepted', 'deleting_objects') THEN
                    DELETE FROM {SCHEMA}.account_erasure_claim_queue
                    WHERE account_erasure_claim_queue.request_id = candidate.request_id;
                    CONTINUE;
                END IF;
                IF request.attempts >= request.max_attempts THEN
                    UPDATE {SCHEMA}.account_erasure_requests AS selected SET
                        status = 'needs_attention', error_code = 'attempts_exhausted',
                        lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE selected.id = candidate.request_id
                      AND selected.owner_user_id = candidate.owner_user_id;
                    CONTINUE;
                END IF;

                RETURN QUERY
                UPDATE {SCHEMA}.account_erasure_requests AS selected SET
                    status = 'deleting_objects', attempts = selected.attempts + 1,
                    lease_token = gen_random_uuid(), worker_id = p_worker_id,
                    lease_expires_at = CURRENT_TIMESTAMP
                        + pg_catalog.make_interval(secs => p_lease_seconds),
                    error_code = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE selected.id = candidate.request_id
                  AND selected.owner_user_id = candidate.owner_user_id
                RETURNING selected.id, selected.owner_user_id,
                    selected.lease_token, selected.lease_expires_at;
                RETURN;
            END LOOP;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.account_erasure_object_batch(
            p_request_id uuid, p_owner_user_id bigint,
            p_lease_token uuid, p_limit integer
        )
        RETURNS TABLE (object_id uuid, object_key text)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            request {SCHEMA}.account_erasure_requests%ROWTYPE;
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}' THEN
                RAISE EXCEPTION 'account erasure object access is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_request_id IS NULL OR p_owner_user_id IS NULL
               OR p_owner_user_id <= 0 OR p_lease_token IS NULL
               OR p_limit < 1 OR p_limit > 500 THEN
                RAISE EXCEPTION 'invalid account erasure object request'
                    USING ERRCODE = '22023';
            END IF;
            SELECT selected.* INTO request
            FROM {SCHEMA}.account_erasure_requests AS selected
            WHERE selected.id = p_request_id
              AND selected.owner_user_id = p_owner_user_id
            FOR UPDATE;
            IF NOT FOUND OR request.status <> 'deleting_objects'
               OR request.lease_token <> p_lease_token
               OR request.lease_expires_at <= CURRENT_TIMESTAMP THEN
                RAISE EXCEPTION 'account erasure lease is unavailable'
                    USING ERRCODE = '40001';
            END IF;

            PERFORM pg_catalog.set_config(
                'careertrellis.user_id', p_owner_user_id::text, true
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'careertrellis-job-catalog:' || p_owner_user_id::text, 0
                )
            );
            RETURN QUERY
            WITH targets AS (
                SELECT stored.id
                FROM {SCHEMA}.stored_objects AS stored
                WHERE stored.owner_user_id = p_owner_user_id
                  AND stored.erasure_deleted_at IS NULL
                ORDER BY stored.id
                FOR UPDATE
                LIMIT p_limit
            ), tombstoned AS (
                UPDATE {SCHEMA}.stored_objects AS stored
                SET deleted_at = COALESCE(stored.deleted_at, CURRENT_TIMESTAMP)
                FROM targets
                WHERE stored.id = targets.id
                  AND stored.owner_user_id = p_owner_user_id
                RETURNING stored.id, stored.object_key
            )
            SELECT tombstoned.id, tombstoned.object_key
            FROM tombstoned
            ORDER BY tombstoned.id;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.confirm_account_erasure_object_deleted(
            p_request_id uuid, p_owner_user_id bigint,
            p_lease_token uuid, p_object_id uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            request {SCHEMA}.account_erasure_requests%ROWTYPE;
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}' THEN
                RAISE EXCEPTION 'account erasure confirmation is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            SELECT selected.* INTO request
            FROM {SCHEMA}.account_erasure_requests AS selected
            WHERE selected.id = p_request_id
              AND selected.owner_user_id = p_owner_user_id
            FOR UPDATE;
            IF NOT FOUND OR request.status <> 'deleting_objects'
               OR request.lease_token <> p_lease_token
               OR request.lease_expires_at <= CURRENT_TIMESTAMP THEN
                RETURN FALSE;
            END IF;
            PERFORM pg_catalog.set_config(
                'careertrellis.user_id', p_owner_user_id::text, true
            );
            UPDATE {SCHEMA}.stored_objects AS stored
            SET erasure_deleted_at = COALESCE(
                stored.erasure_deleted_at, CURRENT_TIMESTAMP
            )
            WHERE stored.id = p_object_id
              AND stored.owner_user_id = p_owner_user_id
              AND stored.deleted_at IS NOT NULL;
            RETURN FOUND;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.renew_account_erasure_lease(
            p_request_id uuid, p_owner_user_id bigint,
            p_lease_token uuid, p_lease_seconds integer
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}'
               OR p_lease_seconds < 30 OR p_lease_seconds > 900 THEN
                RETURN FALSE;
            END IF;
            UPDATE {SCHEMA}.account_erasure_requests AS request SET
                lease_expires_at = CURRENT_TIMESTAMP
                    + pg_catalog.make_interval(secs => p_lease_seconds),
                updated_at = CURRENT_TIMESTAMP
            WHERE request.id = p_request_id
              AND request.owner_user_id = p_owner_user_id
              AND request.status = 'deleting_objects'
              AND request.lease_token = p_lease_token
              AND request.lease_expires_at > CURRENT_TIMESTAMP;
            RETURN FOUND;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.retry_account_erasure(
            p_request_id uuid, p_owner_user_id bigint,
            p_lease_token uuid, p_error_code text
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}'
               OR p_error_code NOT IN ('object_delete_failed', 'persistence_unavailable') THEN
                RETURN FALSE;
            END IF;
            UPDATE {SCHEMA}.account_erasure_requests AS request SET
                status = CASE WHEN request.attempts >= request.max_attempts
                              THEN 'needs_attention' ELSE 'accepted' END,
                error_code = CASE WHEN request.attempts >= request.max_attempts
                                  THEN 'attempts_exhausted' ELSE p_error_code END,
                available_at = CURRENT_TIMESTAMP + pg_catalog.make_interval(
                    secs => LEAST(900, (2 ^ LEAST(request.attempts, 9))::integer)
                ),
                lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE request.id = p_request_id
              AND request.owner_user_id = p_owner_user_id
              AND request.status = 'deleting_objects'
              AND request.lease_token = p_lease_token
              AND request.lease_expires_at > CURRENT_TIMESTAMP;
            RETURN FOUND;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.finalize_account_erasure(
            p_request_id uuid, p_owner_user_id bigint, p_lease_token uuid
        ) RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            request {SCHEMA}.account_erasure_requests%ROWTYPE;
            completion_time timestamptz;
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}' THEN
                RAISE EXCEPTION 'account erasure finalization is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            SELECT selected.* INTO request
            FROM {SCHEMA}.account_erasure_requests AS selected
            WHERE selected.id = p_request_id
              AND selected.owner_user_id = p_owner_user_id
            FOR UPDATE;
            IF NOT FOUND OR request.status <> 'deleting_objects'
               OR request.lease_token <> p_lease_token
               OR request.lease_expires_at <= CURRENT_TIMESTAMP THEN
                RETURN FALSE;
            END IF;
            PERFORM pg_catalog.set_config(
                'careertrellis.user_id', p_owner_user_id::text, true
            );
            PERFORM pg_catalog.pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'careertrellis-job-catalog:' || p_owner_user_id::text, 0
                )
            );
            PERFORM 1 FROM {SCHEMA}.app_users AS app_user
            WHERE app_user.id = p_owner_user_id
              AND app_user.disabled_at IS NOT NULL
            FOR UPDATE;
            IF NOT FOUND THEN
                RETURN FALSE;
            END IF;
            IF EXISTS (
                SELECT 1 FROM {SCHEMA}.stored_objects AS stored
                WHERE stored.owner_user_id = p_owner_user_id
                  AND stored.erasure_deleted_at IS NULL
            ) THEN
                UPDATE {SCHEMA}.account_erasure_requests AS selected SET
                    status = 'accepted', available_at = CURRENT_TIMESTAMP,
                    lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE selected.id = p_request_id
                  AND selected.owner_user_id = p_owner_user_id;
                RETURN FALSE;
            END IF;

            -- Deleting the identity is the authoritative cascade. It removes
            -- every tenant row, makes stale post-acceptance writers fail their
            -- owner FK, and leaves only this request through ON DELETE SET NULL.
            DELETE FROM careertrellis.app_users AS app_user
            WHERE app_user.id = p_owner_user_id;
            IF NOT FOUND THEN
                RETURN FALSE;
            END IF;
            completion_time := CURRENT_TIMESTAMP;
            UPDATE {SCHEMA}.account_erasure_requests AS selected SET
                status = 'completed', completed_at = completion_time,
                receipt_expires_at = completion_time + INTERVAL '30 days',
                error_code = NULL, lease_token = NULL,
                lease_expires_at = NULL, worker_id = NULL,
                updated_at = completion_time
            WHERE selected.id = p_request_id
              AND selected.owner_user_id IS NULL;
            RETURN FOUND;
        END;
        $$
        """
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.purge_expired_account_erasures(p_limit integer)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            purged integer;
        BEGIN
            IF SESSION_USER <> '{ERASURE_WORKER_ROLE}' THEN
                RAISE EXCEPTION 'account erasure receipt purge is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_limit < 1 OR p_limit > 500 THEN
                RAISE EXCEPTION 'invalid account erasure receipt purge limit'
                    USING ERRCODE = '22023';
            END IF;
            WITH expired AS (
                SELECT request.id
                FROM {SCHEMA}.account_erasure_requests AS request
                WHERE request.status = 'completed'
                  AND request.receipt_expires_at <= CURRENT_TIMESTAMP
                ORDER BY request.receipt_expires_at, request.id
                FOR UPDATE SKIP LOCKED
                LIMIT p_limit
            )
            DELETE FROM {SCHEMA}.account_erasure_requests AS request
            USING expired
            WHERE request.id = expired.id;
            GET DIAGNOSTICS purged = ROW_COUNT;
            RETURN purged;
        END;
        $$
        """
    )

    # Default privileges grant new objects to existing login roles; strip all
    # direct access so every destructive cross-owner action is lease-bound.
    op.execute(
        f"REVOKE ALL ON {SCHEMA}.account_erasure_requests, "
        f"{SCHEMA}.account_erasure_claim_queue FROM PUBLIC, {RUNTIME_ROLE}, "
        f"{SEARCH_WORKER_ROLE}, {ERASURE_WORKER_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {ERASURE_WORKER_ROLE}"
    )
    op.execute(
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {SCHEMA} FROM {ERASURE_WORKER_ROLE}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ERASURE_WORKER_ROLE}")

    signatures = (
        "sync_account_erasure_claim_queue()",
        "request_account_erasure()",
        "claim_next_account_erasure(uuid, integer)",
        "account_erasure_object_batch(uuid, bigint, uuid, integer)",
        "confirm_account_erasure_object_deleted(uuid, bigint, uuid, uuid)",
        "renew_account_erasure_lease(uuid, bigint, uuid, integer)",
        "retry_account_erasure(uuid, bigint, uuid, text)",
        "finalize_account_erasure(uuid, bigint, uuid)",
        "purge_expired_account_erasures(integer)",
    )
    for signature in signatures:
        _revoke_function(signature)
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {SCHEMA}.request_account_erasure() "
        f"TO {RUNTIME_ROLE}"
    )
    for signature in signatures[2:]:
        op.execute(
            f"GRANT EXECUTE ON FUNCTION {SCHEMA}.{signature} "
            f"TO {ERASURE_WORKER_ROLE}"
        )


def downgrade() -> None:
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.purge_expired_account_erasures(integer)"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.finalize_account_erasure(uuid, bigint, uuid)"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.retry_account_erasure(uuid, bigint, uuid, text)"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.renew_account_erasure_lease(uuid, bigint, uuid, integer)"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.confirm_account_erasure_object_deleted(uuid, bigint, uuid, uuid)"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.account_erasure_object_batch(uuid, bigint, uuid, integer)"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.claim_next_account_erasure(uuid, integer)"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.request_account_erasure()")
    op.execute(
        f"DROP TRIGGER IF EXISTS account_erasure_claim_queue_sync "
        f"ON {SCHEMA}.account_erasure_requests"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.sync_account_erasure_claim_queue()"
    )
    op.drop_table("account_erasure_claim_queue", schema=SCHEMA)
    op.drop_index(
        "idx_account_erasure_requests_receipt_expiry",
        table_name="account_erasure_requests",
        schema=SCHEMA,
    )
    op.drop_table("account_erasure_requests", schema=SCHEMA)
    op.drop_column("stored_objects", "erasure_deleted_at", schema=SCHEMA)
