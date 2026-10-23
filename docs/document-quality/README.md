# Final PDF byte diagnostics

QUALITY-002 starts with an offline, read-only diagnostic of the bytes a caller
supplies. It is not connected to the application's download route or UI yet.
No existing document is regenerated, modified or replaced by this checker.

The current renderer (`backend/utils.py`) already tags Chromium PDFs, writes
bounded metadata, counts pages after metadata processing, and tries one compact
layout before refusing an ordinary resume above two pages or letter above one.
That protects page count; it does not establish correct final extraction, contact
fields or visible layout. This slice checks additional properties of final bytes
using the existing PyMuPDF dependency, without copying upstream implementation.

`scripts/document_quality.py` exposes `inspect_pdf(bytes, ...)` and
`inspect_file(path, ...)`. Supply `expected_text` as a mapping of safe labels to
required contact fields or other expected text. Reports contain labels, counts,
page numbers, source SHA-256 and fixed diagnostic codes; they omit document text,
metadata and source paths. Missing expectations are skipped rather than passed.

Measured checks are PDF decoding, document-specific page count, nonempty text on
every page, a configurable total extraction floor, required text presence, and
text span bounds crossing a page edge. Ordinary resumes default to two pages,
letters to one; academic CVs have no fixed page cap. The default extraction floor
of 40 non-whitespace characters detects blank or tiny output, not writing quality.
The one-point boundary tolerance is an explicit geometric tolerance, not a
calibrated scoring threshold for all professions/templates.

Extractable text can still be invisible or misleading. The checker deliberately
marks visual readability, human reading order, clipping within the page, orphaned
headings and excessive gaps as skipped pending rendered review and template
calibration. A valid synthetic document therefore reports `incomplete`, not a full
quality pass. A known measured failure reports `failed`. No result makes an ATS
compatibility guarantee or closes QUALITY-002's live download acceptance.

Unreadable/password-protected PDFs are failures; unavailable extraction is a
skipped check. A missing decoder is skipped, not evidence of a broken document.
Inputs are bounded to 20 MiB and 100 pages to keep this offline diagnostic narrow.
Neither file extension nor a successful page count is treated as text integrity.

Run the synthetic tests from repository root with the existing Python environment:

```powershell
python -B -m unittest discover -s scripts -p test_document_quality.py
```

The tests build disposable fabricated PDFs in memory and one temporary directory;
no real resumes, external services, browser or paid calls are used. On the affected
Windows sandbox, run temporary-file suites in the scoped approved context, without
altering ACLs. Generated fixtures are cleaned after each test and never committed.
Retain the source/tests with this review branch; owner CareerTrellis engineering,
next review when integrating the exact downloaded bytes or calibrating templates.
