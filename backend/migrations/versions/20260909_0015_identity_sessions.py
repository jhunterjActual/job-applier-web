"""Durable account session epochs and protected identity transitions.

Installation does not bind a provider, activate v2, or open signup.
"""
from pathlib import Path
from alembic import op
from sqlalchemy import text

revision = '20260909_0015'
down_revision = '20260907_0014'
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
    op.get_bind().execute(text("""DO $$ BEGIN
        IF NOT EXISTS(SELECT 1 FROM careertrellis_admission.policy WHERE maintenance AND NOT signup_open)
           OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN
               ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker') AND rolcanlogin)
           OR EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
               AND usename IN ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')) THEN
            RAISE EXCEPTION 'identity migration requires closed maintenance and quiesced callers';
        END IF;
    END $$"""))
    op.get_bind().execute(text(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')))


def downgrade():
    raise RuntimeError('Identity/session containment requires forward correction.')
