# Historical offline source fixtures

These byte-preserved source fixtures reproduce historical comparisons using
fabricated listings. They are not imported by application runtime code and are
not recommendations to replace current production filters.

- `candidate_filters_baseline.py`: filter source used by the October 7 baseline,
  copied from the retained `serper-open-beta-20261007` worktree. SHA-256:
  `04c3e76e91d55994eed4bce3a900ce654e67b6b952214d00b02b226cd7717f08`.
- `intent-context-candidate.py`: rejected October 9 context experiment, copied
  from the retained experiment source. SHA-256:
  `bd1d14aa9db5577f91b30805b6ecb1188df335b90816faef85a18ecc0f71c0bf`.

Existing comparison receipts and tests verify the exact historical hashes and
outcomes. The unchanged before-context source is `scripts/search_role_intent.py`
at the repository root; it is not duplicated here. Both fixtures were reviewed
as pure deterministic source without credentials or user data on October 11.