"""Default-closed admission and coordinated canonical account lifecycle.

Requires explicit administrator bootstrap and quiesced lifecycle callers.
No identity import, credentials, public routes or provider activation.
"""

from pathlib import Path
from alembic import op
from sqlalchemy import text

revision = '20260907_0014'
down_revision = '20260903_0013'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(text("""DO $$ BEGIN
        IF to_regnamespace('careertrellis_admission') IS NULL
           OR to_regnamespace('careertrellis_lifecycle') IS NULL
           OR NOT pg_has_role(session_user, 'careertrellis_admission_owner', 'SET') THEN
            RAISE EXCEPTION 'explicit admission bootstrap is required';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_namespace WHERE
            (nspname='careertrellis_admission' AND nspowner <> 'careertrellis_admission_owner'::regrole)
            OR (nspname='careertrellis_lifecycle' AND nspowner <> 'careertrellis_schema_owner'::regrole)) THEN
            RAISE EXCEPTION 'admission schema owner mismatch';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles r WHERE r.rolname IN
            ('careertrellis_admission_owner','careertrellis_admission_broker')
            AND (r.rolcanlogin OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolinherit OR r.rolreplication OR r.rolbypassrls))
           OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE
               m.member IN ('careertrellis_admission_owner'::regrole,'careertrellis_admission_broker'::regrole)
               OR (m.roleid IN ('careertrellis_admission_owner'::regrole,'careertrellis_admission_broker'::regrole)
                   AND (m.roleid<>'careertrellis_admission_owner'::regrole OR m.member<>'careertrellis_migration'::regrole
                        OR m.inherit_option OR NOT m.set_option OR m.admin_option))) THEN
            RAISE EXCEPTION 'admission role contract failed';
        END IF;
        IF EXISTS (SELECT 1 FROM pg_namespace n, LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a
                   WHERE n.nspname IN ('careertrellis_admission','careertrellis_lifecycle') AND a.grantee<>n.nspowner)
           OR EXISTS (SELECT 1 FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
               WHERE d.defaclrole='careertrellis_admission_owner'::regrole AND a.grantee<>d.defaclrole) THEN
            RAISE EXCEPTION 'admission privilege contract failed';
        END IF;
    END $$"""))
    # Moving an implementation preserves its OID. Reject catalog dependencies
    # that could continue invoking the old implementation behind the new wrapper.
    connection.execute(text("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_depend d JOIN pg_proc p ON p.oid=d.refobjid
            JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE d.refclassid='pg_proc'::regclass AND n.nspname='careertrellis'
            AND p.proname IN ('request_account_erasure','claim_next_account_erasure',
                'account_erasure_object_batch','confirm_account_erasure_object_deleted',
                'renew_account_erasure_lease','retry_account_erasure',
                'finalize_account_erasure','purge_expired_account_erasures')
        ) THEN RAISE EXCEPTION 'erasure function dependencies require review'; END IF;
    END $$"""))
    connection.execute(text(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8')))


def downgrade():
    raise RuntimeError('Admission lifecycle requires reviewed forward correction; destructive downgrade is disabled.')
