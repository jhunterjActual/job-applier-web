"""Read-only, fixed-output migration prerequisite; never installs credentials."""

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from postgres_database import database_url_from_environment, MIGRATION_DATABASE_URL_ENV


def check(engine):
    with engine.connect() as connection:
        if connection.scalar(text('SELECT session_user')) != 'careertrellis_migration':
            raise RuntimeError('migration principal required')
        if connection.scalar(text("""SELECT count(*) FROM pg_roles WHERE rolname IN
            ('careertrellis_admission_owner','careertrellis_admission_broker') AND NOT rolcanlogin
            AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolinherit
            AND NOT rolreplication AND NOT rolbypassrls""")) != 2:
            raise RuntimeError('bootstrap required')
        if connection.scalar(text("""SELECT count(*) FROM pg_namespace WHERE
            (nspname='careertrellis_admission' AND nspowner='careertrellis_admission_owner'::regrole)
            OR (nspname='careertrellis_lifecycle' AND nspowner='careertrellis_schema_owner'::regrole)""")) != 2:
            raise RuntimeError('bootstrap required')
        if connection.scalar(text("""SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
            AND pid<>pg_backend_pid() AND usename IN ('careertrellis_runtime','careertrellis_worker',
                'careertrellis_erasure_worker','careertrellis_migration','careertrellis_admission_broker')""")):
            raise RuntimeError('lifecycle connections must be quiesced')
        # The migration independently validates exact ACL/membership contracts
        # inside its transaction; this preflight is not mutation authority.


def main():
    engine = None
    try:
        engine = create_engine(database_url_from_environment(MIGRATION_DATABASE_URL_ENV),
                               poolclass=NullPool, hide_parameters=True,
                               connect_args={'connect_timeout': 5})
        check(engine)
    except Exception:
        print('Admission migration preflight failed; bootstrap or quiescence requires review.')
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print('Admission migration preflight passed; no configuration changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
