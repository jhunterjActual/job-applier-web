# Identity-withheld packet preparation

`scripts/value_evaluation/blind_packets.py` provides a read-only-source, create-once
`package(source_root, manifest, output_root)` API. It neither dispatches a study nor
scores outputs. It requires all twelve assigned material slots: CT and UP for
V01–V06. Failed and missing slots remain in the assigned denominator. Earlier
drafts cannot replace a failed native completion.

The strict source manifest declares schema `blind-packet-source-v2`, an exact
study-freeze SHA-256, classification `real_application_comparison`, stage
`first_native_completed_pre_human`, a `shared_inputs` binding and twelve `slots`.
Bindings contain exactly `path` (relative to the explicitly supplied source root)
and `sha256`. Slots contain exactly `product_id`, `case_id`, `status`,
`terminal_receipt`, `resume`, `cover` and `reason_code`. Status is `completed`,
`failed` or `missing`; missing may have no receipt. Failed requires a receipt and
both document bindings null. Completed requires both actual PDF bindings.

Each terminal receipt declares schema `native-completed-application-v2`, matching
classification, freeze, product/case, `outcome`, `scored_stage`,
`human_corrections: false`, and `documents`. Completed documents map `resume` and
`cover` to original SHA-256 values; unsuccessful receipts declare an empty map.
These declarations and hashes establish consistency only. They cannot authenticate
a producer, prove that an output was actually first, or confer execution approval.
An operator must separately verify the native evidence chain. Scripted mechanics
are explicitly refused as scored evidence; synthetic unit controls are not study
results.

The output directory must be new, with an existing parent outside the source
tree. All bindings validate before output creation. Linked/junction paths,
hard-linked files, traversal, changed bytes, duplicate slots and omitted slots
are refused. Inputs are bounded to 2 MiB UTF-8; individual files to 10 MiB and the
total bound inputs to 100 MiB. Source writers must be quiescent; this is a
cooperating local-filesystem workflow, not protection against a hostile writer.

Only the `review/` directory is exportable to reviewers. It contains shared
facts/postings, randomly named derivative PDFs and a manifest in opaque-ID order.
`private/mapping.json` retains the product assignment, original bindings and
equivalence evidence. Keep that sibling away from reviewers until scoring is
locked. The shared input text must be independently inspected to exclude the
reviewer key, expected answers and product identifiers; the tool cannot establish
semantic separation. Caller reason codes stay private.

Derivatives clear standard and XMP metadata and garbage-collect removed objects.
Every page must retain its exact text extraction order, geometry and RGB rendering
hash at 72 dpi. Originals are never opened writable or replaced. Encrypted PDFs,
forms, unsupported page geometry and unreadable documents become unavailable
review slots, preserving the original completion outcome; they are not repaired.
QA must visually inspect every derivative page before review. Text, links, tags
and styling are intentionally preserved, so product identity may still be
inferred. This is identity withholding, not a guarantee of blindness or removal
of every possible identifier.

An interrupted output directory is retained and cannot be resumed or overwritten.
An operation-complete marker proves packaging finished, never quality, funding,
approval or provenance. Keep originals, the private mapping and operation records
until study acceptance and a separately agreed retention decision.

Run the synthetic controls from `scripts/value_evaluation` with
`python -B -m unittest -v test_blind_packets`. They check all-page equivalence,
raw removed-metadata sentinels, immutable originals, unavailable denominators,
binding failures, classification refusal, traversal and create-once behavior.
