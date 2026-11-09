# QUALITY002: one reading-order calibration case

On 25 October 2026, case `QUALITY002-READING-ORDER-001` produced a one-page résumé and a one-page cover letter from the public synthetic pair in [fixtures.json](fixtures.json). Every page was visually inspected against [expected-manifest.json](expected-manifest.json), and its unsorted extracted text and span positions were checked. This case passed those declared manual checks.

The résumé retained all eight declared headings with their following content and all five complete bullets in order. The cover retained its date, salutation, paragraphs and closing in order. Zoë, Café, résumé and naïve remained readable. The declared uppercase title styling and conversion of résumé dash characters to ASCII hyphens were observed. No clipping, overlap or orphaned heading was observed in these two pages; all recorded text-span boxes were inside their pages.

## Exact inputs and outputs

The two JSON files are copied byte-for-byte from the reviewed preparation. Exact-path Git attributes preserve their original bytes, including line endings.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Public synthetic fixtures | 1,892 | `01834c22e7e7efa16a96f191f61b761053e9017a91407e9f6af6427f9e6eaa60` |
| Declared expectations | 2,883 | `536742cfa06e1d9427adec682fb61d0aa3fc05af7cb6f8baf1452f617646357a` |
| Résumé PDF, one page | 59,970 | `c28931997359ecdfb6a4e6180b1c843f3575eabd29fbc68765998faa090ddf38` |
| Cover PDF, one page | 35,749 | `e172d9bd598625ba78aa841c17431713f1b9df4258476a6b223680e647a64ced` |

PDFs, page images and raw execution receipts are retained by the operator and are deliberately not included in this change. Their digests identify the reviewed evidence; they do not make that evidence available to GitHub reviewers. Independent inspection requires the operator to provide those retained artifacts separately.

## Tested source and method

The actual calibration used local combined candidate `4f3aa49211879618a8c00c7e097883b442547b76`. This documentation branch starts from hosted main and does not introduce that combined candidate's runtime changes or claim that its base was rendered anew.

| Tested source | SHA-256 |
| --- | --- |
| `backend/utils.py` | `d7f6c7c1510ac317237dda0b1bfb7c498c12d02ce6f20905bbd4f2d6cdc8c955` |
| `scripts/document_quality.py` | `1c346eb5f1e37cfc829ec04ba21d17f0d41b5cc9ccf1f06be8f9a93c81ab4e8b` |
| Operator-local calibration harness | `0079748a21a56cccef645ca94b8d52b2309f7db715438422364e78e4fe94a2dd` |

The harness selected unchanged renderer function bodies, safe imports and constants from the original source using an explicit AST allowlist. It excluded unrelated configuration/provider imports and functions. It did not import or exercise the full backend. The retained source inventory binds the selected function bodies. This change supplies the inputs and report, not a product rendering harness or a complete reproduction environment.

The observed runtime used Python 3.12.14, PyMuPDF 1.26.7, Playwright 1.60.0 and the existing pinned Chromium. Browser requests were intercepted and aborted; that is not OS network isolation. No database, provider, paid API or real applicant data was used. A bounded Windows owner attached the worker to its job before releasing the rendering payload and recorded natural exit zero, no active job members, closed handles and no capture errors. Its deadline depended on the owner remaining alive; it was not crash-proof containment or an OS hard deadline.

## Retained review bindings

These are digests of operator-local records, not public links or artifacts shipped in this repository.

| Record | SHA-256 |
| --- | --- |
| Primary actual readback | `c4c9097e2b7cf43ae6cfd85ae5064aff7026c4c6bfb7db6cd9783ffe79ccbb46` |
| Independent QA actual case and every-page visual review | `98eef2aedc43b62b1a85916056b94ec7517a1b7df6ce576e5af6636f0d353986` |
| Independent architecture actual owner/evidence review | `778869c83af8cae5b69853ea673b50812872c5512935e27be311134320da3236` |
| Calibration source freeze | `7eb67543bd8fdbf660e7d7243c84a5c804bab7089457341faae18097063fd568` |

QA visually reviewed both complete page images and compared all allowlisted export hashes, text, facts and bounding boxes. Architecture independently checked source/runtime associations, retained owner lifecycle evidence and output hashes. The primary also inspected both pages and verified the retained evidence. These reviews assess this fixed pair and the recorded execution boundaries.

## Remaining acceptance boundaries

The expectation file's original `manual_status: not_run` and the generated diagnostics' original manual-not-run/skipped/incomplete classifications were preserved. The later manual review is a separate case-specific observation; it does not silently upgrade automated diagnostics.

One synthetic pair does not establish generalized reading-order thresholds, ATS compatibility, scored comparative quality, full-backend behavior, application delivery, native fit workflow acceptance or paid-study acceptance. Additional document shapes, page breaks and adverse layouts remain outside this case. QUALITY002 as a whole is not declared complete by this report.
