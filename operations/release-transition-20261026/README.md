# Inactive application release preparation

This focused operational bundle prepares immutable app97 and forward-correction98 runtimes only. It does not activate them. It contains no recurring monitor, application transition coordinator, policy rebind, installed helper/unit, Caddy or application service changes.

Runtime sources are preparation/collect_existing_provenance.py, preparation/prepare_release.py and preparation/run_checks.py. Only prepare_release.py and run_checks.py are copied to the fixed native preparation stage; provenance collection remains a separately reviewed read-only observation. Git-blob verification binds preparation sources to this separate operations commit. Source archives and observed provenance are external immutable inputs, not packaged private data.

Run portable checks from preparation with an existing Python: python -B -m unittest test_collector test_preparation test_opened_readonly test_empty_collection test_extract_v5_controls. No provider, host or native unit calls occur in these checks.

Three original local-evidence checks are deliberately excluded from this portable test file: exact registered source archives/dependency locks, actual observed runtime provenance, and historical probe source embedding. Their original bytes and results remain in the campaign evidence and were not counted as portable passes. Do not invent missing inputs or use historical probes for execution.

## Native evidence qualification

One reviewed V5 synthetic systemd transition ran. Its exact source SHA is82adc6947366a70243836c0164c17470b73cd9b2a0f39d715ef287a0bb01e6f6. Maintained LF transport request SHA is7af1ae2b8e6daf78ddcf7c776cf76dbc999abbe9d84f8a7c903c19d668504118; executable AST matches (raw doubled-newline docstring formatting differs). Source and transport were preserved before invocation.

All seven identity/network/privilege/read-only mount/bind/owned-write controls passed with11ordered stages and unchanged3synthetic inputs. A separate read-only extractor authenticated retained controls: stdout SHA1396f6aa80418987ba8021bfb0063186be1fcb2e7e7906b76db5a7f668a1c02f; extractor source SHA3b9f6803a0c468814d4b2d73d8545c99a88524069abadc2f272d555b71f1edd5. Independent inspection proved original owner/client absent, captured pipes EOF and task unit/cgroup terminal/absent. Exact native evidence is retained in SANDBOX_V5_CONTROLS.stdout.txt and SANDBOX_V5_INSPECTION.stdout.txt of the campaign evidence, alongside preserved sources.

The OVERALL V5 WRAPPER FAILED at P60_EMPTY_COLLECTION. It incorrectly applied a loaded-unit InvocationID comparison to a collected not-found unit. The diagnosed preparation-only source correction guards that comparison with `inactive and not gone`; pure controls reproduce the old failure and preserve loaded drift/PID/job/client failure refusals. Corrected collection has pure-test evidence; full native preparation has NOT run or passed. No V5 rerun was used to relabel the failed wrapper.

The primary accepted the existing native minimum fixed transition evidence for unchanged isolation controls, separately from wrapper completion and full inactive preparation. All invocation, ownership, storage, exact plan/source, disk/inode, native preparation receipts and independent final review gates remain required. This source is not installation or activation authority.
