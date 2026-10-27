# Reviewed release transition source and portable regressions

This new prefix stacks from a62e3f1269df9390d075a263385dc024b2623d6a. It contains35 runtime/nested files, two unchanged native policy fixtures and the exact reviewed build_manifest.py (38 source files). Runtime bytes, original line endings and authentic public fixture bytes are preserved. PUBLIC_RUNTIME_SOURCE_PINS.json verifies all38 before portable test imports.

Run from this directory: python -B -m unittest discover -s tests -p 'test*.py'. The54 portable cases retain28 historical-consumer,18 engine,6 original source/authority/runtime guards and2 cached public-source retrieval tests. Consumer receipt graphs are synthetic, including when bound to real public ops identities. No actual acceptance, private inventory, profile, provider record or plan is a test fixture.

The original55th case requires an actual local291d Git object and remains external-only; it is not skipped or counted as portablePASS. Its public fixture digest remains covered by the pure source/history validators. All existing engine and extracted guard test-method assertions remain unchanged; engine setup alone moves to the reviewed closed v2 plan fixture. Package-local bootstrap verifies source bytes and installs explicit import aliases. Six copied public pure dependencies remain exact hash/source fixtures for an unchanged provenance regression.

Optional builder contract checks: python -B -m unittest discover -s tests -p 'builder_contract_checks.py'. Seven cases use mocked Git and synthetic tiny sources/outputs. They do not create an actual runtime manifest, prove actual Git ancestry or supply an operational commit. Actual builder generation still requires the reviewed source commit and every actual runtime/nested Git blob.

Scoped .gitattributes is a proposal for byte preservation: exact source paths use -text; only actual rawCRLFpaths add cr-at-eol. No blanket whitespace or EOF exception is included. Authentic EOF/trailing-horizontal warnings require explicit parent review, not normalization.

Current-target follow-up: accepted_pair_validator.py and operator/drive.py bind the corrected bc68 candidate. Historical baseline receipt hashes and all safety methods remain unchanged. The operator bridge is separate from the35-file runtime packet.

Run the12 target-context regressions explicitly: python -B -m unittest discover -s tests/target-context-rebind -p 'test*.py'. These include actual published-byte bindings and the complete active-target audit; they do not invoke real paired receipts or hosts.
