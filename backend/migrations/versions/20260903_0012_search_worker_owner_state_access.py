"""Grant the search worker bounded owner-state access.

Revision ID: 20260903_0012
Revises: 20260902_0011
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0012"
down_revision: Union[str, Sequence[str], None] = "20260902_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
SEARCH_WORKER_ROLE = "careertrellis_worker"


def upgrade() -> None:
    op.execute(
        f"REVOKE SELECT ON {SCHEMA}.app_users FROM {SEARCH_WORKER_ROLE}"
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.lock_search_run_claim(
            p_run_id uuid,
            p_owner_user_id bigint,
            p_lease_token uuid
        )
        RETURNS TABLE (run_status varchar(24), owner_disabled boolean)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF SESSION_USER <> '{SEARCH_WORKER_ROLE}' THEN
                RAISE EXCEPTION 'search worker claim lock is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_run_id IS NULL OR p_owner_user_id IS NULL
               OR p_owner_user_id <= 0 OR p_lease_token IS NULL THEN
                RAISE EXCEPTION 'invalid search worker claim lock'
                    USING ERRCODE = '22023';
            END IF;
            IF current_setting('careertrellis.user_id', true)
                    IS DISTINCT FROM p_owner_user_id::text THEN
                RAISE EXCEPTION 'search worker tenant context is invalid'
                    USING ERRCODE = '42501';
            END IF;

            RETURN QUERY
            SELECT search_run.status,
                   app_user.disabled_at IS NOT NULL
            FROM {SCHEMA}.search_runs AS search_run
            JOIN {SCHEMA}.app_users AS app_user
              ON app_user.id = search_run.owner_user_id
            WHERE search_run.id = p_run_id
              AND search_run.owner_user_id = p_owner_user_id
              AND search_run.lease_token = p_lease_token
              AND search_run.lease_expires_at > CURRENT_TIMESTAMP
            FOR UPDATE OF search_run FOR SHARE OF app_user;
        END;
        $$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.lock_search_run_claim(uuid, bigint, uuid) "
        "FROM PUBLIC, careertrellis_runtime, careertrellis_worker, "
        "careertrellis_erasure_worker"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {SCHEMA}.lock_search_run_claim(uuid, bigint, uuid) "
        f"TO {SEARCH_WORKER_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.lock_search_run_claim(uuid, bigint, uuid)"
    )
