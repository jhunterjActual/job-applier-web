from __future__ import annotations

import os
from pathlib import Path
import sys

import psycopg
from sqlalchemy.engine import make_url


FIXED_ERROR = "The reviewed PostgreSQL runtime grants could not be applied."


def apply_runtime_grants(sql_path: Path) -> None:
    migration_url = os.environ.get("CAREERTRELLIS_MIGRATION_DATABASE_URL", "")
    if not migration_url:
        raise RuntimeError(FIXED_ERROR)

    url = make_url(migration_url)
    if url.drivername != "postgresql+psycopg":
        raise RuntimeError(FIXED_ERROR)
    sql_text = sql_path.read_text(encoding="utf-8")
    lines = sql_text.splitlines()
    if not lines or lines[0].strip() != r"\set ON_ERROR_STOP on":
        raise RuntimeError(FIXED_ERROR)
    sql_text = "\n".join(lines[1:]).strip()
    if any(line.lstrip().startswith("\\") for line in sql_text.splitlines()):
        raise RuntimeError(FIXED_ERROR)

    connection_url = url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    try:
        with psycopg.connect(connection_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql_text, prepare=False)
    except Exception as exc:
        raise RuntimeError(FIXED_ERROR) from exc


def main() -> int:
    sql_path = (
        Path(__file__).resolve().parent.parent
        / "deploy"
        / "postgres"
        / "grant-runtime.sql"
    )
    try:
        apply_runtime_grants(sql_path)
    except Exception:
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    print("The reviewed PostgreSQL runtime grants were applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
