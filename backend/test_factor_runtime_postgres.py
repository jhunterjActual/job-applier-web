"""Structural readiness against a dedicated synthetic PostgreSQL fixture."""
from sqlalchemy import text

from factor_runtime import factor_readiness
from test_removal_session_postgres import SessionRemovalTests


class FactorReadinessPostgresTests(SessionRemovalTests):
    async def readiness(self):
        class Query:
            async def _query(inner,engine,sql,*,mapping):
                async with engine.begin() as connection:
                    return dict((await connection.execute(text(sql))).mappings().one())
        await factor_readiness(Query(),self.runtime)

    async def test_restricted_runtime_readiness_and_corruption_fences(self):
        await self.readiness()
        signature='careertrellis.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)'
        cases=(
            ("UPDATE careertrellis.alembic_version SET version_num='20260929_0019'",
             "UPDATE careertrellis.alembic_version SET version_num='20260929_0020'"),
            ('ALTER TABLE careertrellis_admission.factor_removal_attempts ALTER COLUMN initiating_session_key DROP NOT NULL',
             'ALTER TABLE careertrellis_admission.factor_removal_attempts ALTER COLUMN initiating_session_key SET NOT NULL'),
            ('REVOKE EXECUTE ON FUNCTION '+signature+' FROM careertrellis_runtime',
             'GRANT EXECUTE ON FUNCTION '+signature+' TO careertrellis_runtime'),
            ('GRANT EXECUTE ON FUNCTION '+signature+' TO careertrellis_worker',
             'REVOKE EXECUTE ON FUNCTION '+signature+' FROM careertrellis_worker'),
            ('ALTER FUNCTION '+signature+' RENAME TO factor_removal_request_unready',
             'ALTER FUNCTION careertrellis.factor_removal_request_unready(jsonb,text,text,uuid,jsonb,jsonb) RENAME TO factor_removal_request'),
        )
        for damage,restore in cases:
            with self.subTest(damage=damage):
                self.query(damage)
                try:
                    with self.assertRaises(ValueError): await self.readiness()
                finally:
                    self.query(restore)
                await self.readiness()
