# Offline evidence integrity and denominators

`scripts/value_evidence.py` reads an explicit inventory of synthetic operation
receipts and checks their hashes and referenced product-output hashes. It reports
declared, missing, invalid, blocked, attempted, successful-output, failed-attempt
and unresolved-billing counts separately. A successful output with unknown billing
still contributes to the successful-output count, but never to a complete actual
cost total. Retained reservations remain exposure estimates rather than charges.

This is an offline VALUE-001 measurement aid, not comparative evaluation acceptance.
It makes no provider calls, imports no application configuration, writes no files,
and never searches a directory for undeclared receipts. Product content is hashed
without being printed. Use only synthetic task evidence, not private user data.

Create and independently review a JSON inventory using this shape:

```json
{
  "schema": "synthetic-evidence-inventory-v1",
  "operations": [
    {
      "call_id": "CT-V01-fit-a1",
      "receipt": "CT-V01-fit-a1/receipt.json",
      "sha256": "replace with the exact 64-character lowercase receipt SHA-256"
    }
  ]
}
```

Declare missing operations as well as completed operations, using the expected
receipt path and a declared placeholder digest when no receipt exists. The tool
reports missing before evaluating that digest. Declare both attempts separately;
never substitute the best result for a failed earlier attempt. The inventory
must include one distinct receipt per operation. Review expected case/product/
operation/attempt coverage against the frozen protocol; this tool cannot discover
an omitted operation or authenticate an inventory supplied by its author.

Each receipt uses `call_id`, integer `generation_dispatches` (zero or one),
`outcome`, `actual_usd`, and `reservation_usd`. Amounts are decimal strings with
at most six fractional digits or null when unavailable. Zero dispatches requires
`blocked_before_dispatch`; one dispatch accepts `success`, `attempted_failure`,
`outcome_unknown`, or `paused_pending_reconciliation`. A success requires
`product_output_sha256`, referring to `product-output.json` beside the receipt.
Other dispatched outcomes may retain a verified successful output independently
of billing settlement. Unknown cost keeps `complete_actual_cost_usd` null.

Run with the repository Python interpreter:

```powershell
./backend/venv/Scripts/python.exe -B scripts/value_evidence.py --root <synthetic-evidence-directory> --inventory <reviewed-inventory.json>
./backend/venv/Scripts/python.exe -B -m unittest discover -s scripts -p test_value_evidence.py
```

Exit zero means the declared inventory is readable and no declared operation is
missing, invalid or unresolved; it does not mean every operation succeeded or the
comparison is complete. Exit two indicates incomplete evidence or refused input.
The JSON summary distinguishes these conditions. Relative paths cannot escape the
evidence root or traverse detected symlinks/junctions. Evidence files are bounded
to 2 MiB. This is a local reviewer utility, not a concurrent hostile-filesystem
security boundary: freeze evidence and do not edit files during inspection.

No latency, human editing effort, discovery, participant, factual-quality, visual
quality or winner claim follows from the output. Product success here means only
that a declared generated artifact matches its retained digest. Ledger budget
enforcement and operational reconciliation remain the runner's responsibility.
