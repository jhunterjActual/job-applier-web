#!/usr/bin/env bash
set -Eeuo pipefail
set +x
# This candidate requires receipt-bound admission coordination. Retain the
# historical entry point, but never read, source, or export its credentials,
# even for an older target. Historical immutable releases are unchanged.
echo "Standalone migration/grants are unavailable in this revision. Use the reviewed admission coordinator." >&2
exit 1
