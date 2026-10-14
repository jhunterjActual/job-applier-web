# Provider Credential Links

CareerTrellis exposes fixed links from **Profile & Resume** to the official pages where a user can create, review, rotate, or revoke provider API keys. These destinations are an allowlist: they must never be built from user input, provider responses, credentials, or other profile data.

## Current allowlist

Verified against the providers' official documentation on 2026-09-02.

| Provider | Credential purpose | Destination |
| --- | --- | --- |
| Google Gemini | AI matching and material generation | `https://aistudio.google.com/app/apikey` |
| OpenAI | AI matching and material generation | `https://platform.openai.com/api-keys` |
| Anthropic Claude | AI matching, material generation and resume OCR; added and verified 2026-10-12 against https://platform.claude.com/docs/en/get-api-key | `https://platform.claude.com/settings/keys` |
| Google Places | Employer-headquarters lookup | `https://console.cloud.google.com/apis/credentials` |

The browser opens each destination with `noopener`, `noreferrer`, and a `no-referrer` policy. CareerTrellis never adds query parameters or sends a credential, profile field, or provider selection to these links.

## AI-003 setup slice - October 13, 2026

The local candidate discloses that testing uses the saved API key and may charge
that provider account. Saving settings or keys does not dispatch a test. A test
uses a constant sample prompt, with no resume or job content; its structured-output
result does not establish OCR support or every workflow's acceptance.

Model suggestions follow the selected provider. Existing custom models remain
editable for Gemini/OpenAI, with account access explicitly unverified; Claude
retains its fixed model. Switching providers preserves saved credentials.
Authentication, permissions, quota, model and uncertain-request failures offer
next steps without an automatic payer/provider switch. Concurrent test dispatch
and tests with unsaved settings, missing keys or pending key changes are blocked.

This is BYOK setup only. Managed billing/keyless setup depends on MON-003/004/005.
Claude paid live verification remains deferred. No new models, prices, credential
links, paid requests or hosted deployment are approved by this slice. Existing
allowlist review dates above remain historical.

## UX-002 keyboard and mobile setup - October 13, 2026

The provider-test dialog captures its keyboard opener before disabling it, so
focus returns after success, failure or cancellation. Model guidance is linked
to the model input for accessible descriptions. Profile settings stack into one
column at widths up to 900 pixels, keeping the form usable at phone widths.
Synthetic browser coverage checks dialog focus containment, return focus, model
descriptions and layout at 320, 390, 900 and 1280 pixels. The local suite passes
449 tests with two skips; temporary startup, shutdown and data cleanup pass.
Screenshots and receipts remain in `artifacts/ux002-provider-focus-20261013/`.
Actual assistive-technology, physical-device and hosted acceptance remain open.
Claude live verification remains deferred.

## Review cadence

Reconfirm every destination against the provider's official documentation before a public release and at least every six months. Check that the page still supports credential management rather than only general documentation or billing. Update the centralized `PROVIDER_CREDENTIAL_LINKS` allowlist and `PROVIDER_CREDENTIAL_LINKS_REVIEWED_ON` date in `backend/static/app.js` together, then run the frontend credential-link regression coverage.
