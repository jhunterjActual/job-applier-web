"""Create the tenant-owned PostgreSQL and RLS foundation.

Revision ID: 20260811_0001
Revises: None
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260811_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
TENANT_TABLES = (
    "provider_secrets",
    "stored_objects",
    "profiles",
    "base_resumes",
    "base_resume_versions",
    "jobs",
    "saved_searches",
    "applications",
    "application_status_history",
    "application_engagements",
    "job_suppressions",
    "source_diagnostics",
    "headquarters_cache",
)


def _id_column() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True)


def _owner_column() -> sa.Column:
    return sa.Column(
        "owner_user_id",
        sa.BigInteger(),
        sa.ForeignKey(f"{SCHEMA}.app_users.id", ondelete="CASCADE"),
        nullable=False,
    )


def _timestamp(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("CURRENT_TIMESTAMP") if default else None,
    )


def _enable_tenant_rls(table: str) -> None:
    qualified = f'"{SCHEMA}"."{table}"'
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {qualified}
        USING (owner_user_id = {SCHEMA}.current_app_user_id())
        WITH CHECK (owner_user_id = {SCHEMA}.current_app_user_id())
        """
    )


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.current_app_user_id()
        RETURNS bigint
        LANGUAGE sql
        STABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        RETURN NULLIF(pg_catalog.current_setting('careertrellis.user_id', true), '')::bigint
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.current_workos_user_id()
        RETURNS text
        LANGUAGE sql
        STABLE
        PARALLEL SAFE
        SET search_path = pg_catalog
        RETURN NULLIF(pg_catalog.current_setting('careertrellis.workos_user_id', true), '')
        """
    )

    op.create_table(
        "app_users",
        _id_column(),
        sa.Column("workos_user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),
        _timestamp("created_at", default=True),
        _timestamp("last_seen_at", default=True),
        _timestamp("disabled_at", nullable=True),
        sa.CheckConstraint("role IN ('owner', 'user')", name="ck_app_users_role"),
        sa.UniqueConstraint("workos_user_id", name="uq_app_users_workos_user_id"),
        schema=SCHEMA,
    )
    op.execute(f'ALTER TABLE "{SCHEMA}"."app_users" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{SCHEMA}"."app_users" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY app_user_identity ON {SCHEMA}.app_users
        USING (
            id = {SCHEMA}.current_app_user_id()
            OR workos_user_id = {SCHEMA}.current_workos_user_id()
        )
        WITH CHECK (
            workos_user_id = {SCHEMA}.current_workos_user_id()
            AND (
                {SCHEMA}.current_app_user_id() IS NULL
                OR id = {SCHEMA}.current_app_user_id()
            )
        )
        """
    )

    op.create_table(
        "provider_secrets",
        _id_column(),
        _owner_column(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        _timestamp("revoked_at", nullable=True),
        sa.UniqueConstraint("owner_user_id", "provider", name="uq_provider_secrets_owner_provider"),
        schema=SCHEMA,
    )

    op.create_table(
        "base_resumes",
        _id_column(),
        _owner_column(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("resume_mode", sa.Text(), nullable=False, server_default="general_professional"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_base_resumes_id_owner"),
        schema=SCHEMA,
    )

    op.create_table(
        "stored_objects",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _owner_column(),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("plaintext_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_data_key", sa.LargeBinary(), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("deleted_at", nullable=True),
        sa.CheckConstraint("plaintext_size >= 0", name="ck_stored_objects_plaintext_size"),
        sa.UniqueConstraint("object_key", name="uq_stored_objects_object_key"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_stored_objects_id_owner"),
        schema=SCHEMA,
    )

    op.create_table(
        "profiles",
        _id_column(),
        _owner_column(),
        sa.Column("name", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("github", sa.Text()),
        sa.Column("linkedin", sa.Text()),
        sa.Column("website", sa.Text()),
        sa.Column("suggested_keywords", sa.Text(), nullable=False, server_default=""),
        sa.Column("ai_provider", sa.Text(), nullable=False, server_default="gemini"),
        sa.Column("ai_model", sa.Text(), nullable=False, server_default="gemini-2.5-flash"),
        sa.Column("maps_provider", sa.Text(), nullable=False, server_default="openstreetmap"),
        sa.Column("prefer_us_headquarters", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interface_language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("active_base_resume_id", sa.BigInteger()),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        sa.UniqueConstraint("owner_user_id", name="uq_profiles_owner"),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_profiles_active_resume_owner",
        "profiles",
        "base_resumes",
        ["active_base_resume_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "base_resume_versions",
        _id_column(),
        _owner_column(),
        sa.Column("base_resume_id", sa.BigInteger(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("resume_mode", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _timestamp("created_at", default=True),
        sa.ForeignKeyConstraint(
            ["base_resume_id", "owner_user_id"],
            [f"{SCHEMA}.base_resumes.id", f"{SCHEMA}.base_resumes.owner_user_id"],
            name="fk_base_resume_versions_resume_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "owner_user_id", "base_resume_id", "version_number",
            name="uq_base_resume_versions_owner_version",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "jobs",
        _id_column(),
        _owner_column(),
        sa.Column("title", sa.Text()),
        sa.Column("company", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("match_score", sa.Integer()),
        sa.Column("match_analysis", sa.Text()),
        sa.Column("date_found", sa.Date()),
        sa.Column("status", sa.Text(), nullable=False, server_default="matched"),
        _timestamp("archived_at", nullable=True),
        sa.Column("archived_from_status", sa.Text()),
        _timestamp("last_checked_at", nullable=True),
        sa.Column("is_expired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expiration_reason", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("work_arrangement", sa.Text()),
        sa.Column("employment_type", sa.Text()),
        sa.Column("compensation", sa.Text()),
        sa.Column("source", sa.Text()),
        sa.CheckConstraint(
            "match_score IS NULL OR match_score BETWEEN 0 AND 100",
            name="ck_jobs_match_score",
        ),
        sa.UniqueConstraint("owner_user_id", "url", name="uq_jobs_owner_url"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_jobs_id_owner"),
        schema=SCHEMA,
    )

    op.create_table(
        "saved_searches",
        _id_column(),
        _owner_column(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("keywords", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False, server_default=""),
        _timestamp("created_at", default=True),
        _timestamp("last_run_at", nullable=True),
        sa.Column("schedule_frequency", sa.Text(), nullable=False, server_default="none"),
        _timestamp("next_alert_at", nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "owner_user_id", "keywords", "location", name="uq_saved_searches_owner_query"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "applications",
        _id_column(),
        _owner_column(),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("company", sa.Text()),
        sa.Column("position", sa.Text()),
        sa.Column("date_applied", sa.Date()),
        sa.Column("us_hq", sa.Text()),
        sa.Column("tailored_resume_object_id", sa.Uuid()),
        sa.Column("cover_letter_object_id", sa.Uuid()),
        sa.Column("tailored_resume_text", sa.Text()),
        sa.Column("cover_letter", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="tailored"),
        _timestamp("created_at", default=True),
        _timestamp("tailored_at", nullable=True),
        _timestamp("form_filled_at", nullable=True),
        _timestamp("submitted_at", nullable=True),
        _timestamp("confirmed_at", nullable=True),
        sa.Column("application_method", sa.Text()),
        sa.Column("submission_evidence", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("follow_up_date", sa.Date()),
        sa.Column("headquarters_source", sa.Text()),
        sa.Column("headquarters_attribution", sa.Text()),
        sa.Column("base_resume_id", sa.BigInteger()),
        sa.Column("base_resume_name", sa.Text()),
        sa.Column("base_resume_version", sa.Integer()),
        sa.Column("interview_prep", sa.Text()),
        _timestamp("interview_prep_updated_at", nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            [f"{SCHEMA}.jobs.id", f"{SCHEMA}.jobs.owner_user_id"],
            name="fk_applications_job_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["base_resume_id", "owner_user_id"],
            [f"{SCHEMA}.base_resumes.id", f"{SCHEMA}.base_resumes.owner_user_id"],
            name="fk_applications_base_resume_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tailored_resume_object_id", "owner_user_id"],
            [f"{SCHEMA}.stored_objects.id", f"{SCHEMA}.stored_objects.owner_user_id"],
            name="fk_applications_resume_object_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cover_letter_object_id", "owner_user_id"],
            [f"{SCHEMA}.stored_objects.id", f"{SCHEMA}.stored_objects.owner_user_id"],
            name="fk_applications_letter_object_owner",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("owner_user_id", "job_id", name="uq_applications_owner_job"),
        schema=SCHEMA,
    )

    op.create_table(
        "application_status_history",
        _id_column(),
        _owner_column(),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        _timestamp("changed_at", default=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text()),
        _timestamp("undone_at", nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            [f"{SCHEMA}.jobs.id", f"{SCHEMA}.jobs.owner_user_id"],
            name="fk_application_status_history_job_owner",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "application_engagements",
        _id_column(),
        _owner_column(),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("engagement_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_details", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("activity_on", sa.Date()),
        sa.Column("next_action_on", sa.Date()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            [f"{SCHEMA}.jobs.id", f"{SCHEMA}.jobs.owner_user_id"],
            name="fk_application_engagements_job_owner",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "job_suppressions",
        _id_column(),
        _owner_column(),
        sa.Column("url_fingerprint", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        _timestamp("deleted_at", default=True),
        sa.Column("deletion_source", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "owner_user_id", "url_fingerprint", name="uq_job_suppressions_owner_fingerprint"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "source_diagnostics",
        _id_column(),
        _owner_column(),
        _timestamp("recorded_at", default=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("diagnostic_code", sa.Text(), nullable=False),
        sa.Column(
            "counters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "headquarters_cache",
        _id_column(),
        _owner_column(),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("attribution", sa.Text(), nullable=False, server_default=""),
        _timestamp("resolved_at", default=True),
        sa.UniqueConstraint("owner_user_id", "cache_key", name="uq_headquarters_cache_owner_key"),
        schema=SCHEMA,
    )

    for table in TENANT_TABLES:
        op.create_index(
            f"idx_{table}_owner_user_id",
            table,
            ["owner_user_id"],
            schema=SCHEMA,
        )
        _enable_tenant_rls(table)

    op.create_index(
        "idx_base_resume_versions_owner_resume",
        "base_resume_versions",
        ["owner_user_id", "base_resume_id", sa.text("version_number DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_application_engagements_owner_job_updated",
        "application_engagements",
        ["owner_user_id", "job_id", sa.text("updated_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_source_diagnostics_owner_recorded",
        "source_diagnostics",
        ["owner_user_id", sa.text("recorded_at DESC")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    drop_order = (
        "headquarters_cache",
        "source_diagnostics",
        "job_suppressions",
        "application_engagements",
        "application_status_history",
        "applications",
        "saved_searches",
        "base_resume_versions",
        "profiles",
        "jobs",
        "base_resumes",
        "stored_objects",
        "provider_secrets",
    )
    for table in drop_order:
        op.drop_table(table, schema=SCHEMA)
    op.drop_table("app_users", schema=SCHEMA)
    op.execute(f"DROP FUNCTION {SCHEMA}.current_workos_user_id()")
    op.execute(f"DROP FUNCTION {SCHEMA}.current_app_user_id()")
