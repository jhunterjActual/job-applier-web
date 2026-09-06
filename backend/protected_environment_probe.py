"""Root-only offline configuration compatibility check; never an acceptance run.

Use only from a verified, root-owned candidate with its reviewed dependencies.
No configuration paths or values are accepted from arguments or stdin.
"""

from __future__ import annotations

import os
import sys


FIXED_ERROR = "The protected environment compatibility check did not pass."
FIXED_SUCCESS = "operation=protected_environment_compatibility\nresult=ok\nvalues_disclosed=no"


def check_configuration() -> None:
    # Import inside the CLI privacy boundary so dependency/configuration failures
    # cannot turn into value-bearing tracebacks on the operator's output stream.
    import pilot_acceptance as acceptance
    from object_store import validate_object_store_configuration
    from provider_secrets import validate_provider_secret_store_configuration

    _, migration, runtime, provider, objects = acceptance._load_operator_boundary()
    for value, role in (
        (migration, "careertrellis_migration"),
        (runtime, "careertrellis_runtime"),
    ):
        url = acceptance.postgres_url(value)
        if (
            url.username != role or url.database != "careertrellis"
            or not url.host or not url.password
        ):
            raise acceptance.PilotAcceptanceError(acceptance.FIXED_ERROR)
    with acceptance._temporary_environment({
        **provider, **objects, acceptance.RUNTIME_URL_KEY: runtime,
    }):
        # These validators parse stores, keyrings, endpoint and credentials only;
        # they do not create an engine/client or contact PostgreSQL/R2/WorkOS.
        validate_provider_secret_store_configuration()
        validate_object_store_configuration()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments or getattr(os, "geteuid", lambda: -1)() != 0:
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    # Set before cold imports: config normally prepares runtime directories and
    # Python normally caches bytecode. Neither belongs in a read-only probe.
    setting = "CAREERTRELLIS_CONFIG_READ_ONLY"
    previous = os.environ.get(setting)
    previous_bytecode = sys.dont_write_bytecode
    os.environ[setting] = "1"
    sys.dont_write_bytecode = True
    try:
        check_configuration()
    except (Exception, KeyboardInterrupt):
        print(FIXED_ERROR, file=sys.stderr)
        return 1
    finally:
        if previous is None:
            os.environ.pop(setting, None)
        else:
            os.environ[setting] = previous
        sys.dont_write_bytecode = previous_bytecode
    print(FIXED_SUCCESS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
