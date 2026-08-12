"""Rotate one owner's provider credentials to the active server-side key."""

from __future__ import annotations

import os

from provider_secrets import rotate_provider_secrets, validate_provider_secret_store_configuration


OWNER_ID_ENVIRONMENT_VARIABLE = "CAREERTRELLIS_ROTATE_OWNER_USER_ID"


def main() -> int:
    validate_provider_secret_store_configuration()
    owner_user_id = os.environ.get(OWNER_ID_ENVIRONMENT_VARIABLE, "").strip()
    if not owner_user_id:
        raise RuntimeError(f"{OWNER_ID_ENVIRONMENT_VARIABLE} is required.")
    rotated = rotate_provider_secrets(owner_user_id)
    print(f"Rotated {rotated} provider credential(s) to the active key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
