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

## Review cadence

Reconfirm every destination against the provider's official documentation before a public release and at least every six months. Check that the page still supports credential management rather than only general documentation or billing. Update the centralized `PROVIDER_CREDENTIAL_LINKS` allowlist and `PROVIDER_CREDENTIAL_LINKS_REVIEWED_ON` date in `backend/static/app.js` together, then run the frontend credential-link regression coverage.
