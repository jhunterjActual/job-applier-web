"""Durable protected recovery effects and atomic release."""
from pathlib import Path
from alembic import op
from sqlalchemy import text

revision = '20261005_0023'
down_revision = '20261005_0022'
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
    op.get_bind().execute(text("""DO $$ BEGIN
      IF session_user <> 'careertrellis_migration' OR
       NOT EXISTS(SELECT 1 FROM careertrellis_admission.policy WHERE maintenance AND NOT signup_open)
       OR EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
        AND usename IN ('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker'))
      THEN RAISE EXCEPTION 'Recovery release migration requires closed maintenance and quiesced callers'; END IF;
    END $$"""))
    op.get_bind().execute(text(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')))


def downgrade():
    raise RuntimeError('Recovery effects require compatible forward correction.')
