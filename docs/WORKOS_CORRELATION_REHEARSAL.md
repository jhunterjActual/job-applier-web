# Isolated WorkOS correlation rehearsal

CareerTrellis is owned by **J. Hunter Johnson LLC**. The operator approved an
isolated rehearsal, not a change to pilot staging or public signup activation.

## Current external state

On 2026-09-07, after explicit approval at submission, created a new WorkOS
project **CareerTrellis Auth Rehearsal** using **Start from scratch**, with only
**Staging** included and **Production** unchecked. Readback confirmed the new
project and a single Staging choice. Its separate default application exists.
No existing environment was transferred; no billing activation was performed.
The application's Redirect URIs initially read **Not set**. The operator added
`http://127.0.0.1:8765/callback`; subsequent dashboard readback confirmed that
exact URI under **CareerTrellis Auth Rehearsal**, with no open edit dialog and
the other six redirect/settings rows still **Not set**. Loopback port 8765 was
free at operator-launch preflight. No server or hidden prompt has been started
by the assistant. The operator subsequently reported successful protocol
correlation first using an existing WorkOS account, then with a new test email
account; see the sanitized receipts below. The operator confirmed that the
second flow required email verification. The fresh-signup, subsequent
login-only, expiry and clean-result missing-browser-context checks have passed
on operator-reported evidence. A live swapped callback, duplicate callback,
provider cancellation and Google flow have not been demonstrated.

## What this proof establishes

The loopback harness in `backend/signup_prototype/workos_rehearsal.py` joins an
explicit local signup intent and volatile reservation reference to the provider
callback via browser context, opaque state and the original S256 verifier. It
exchanges the code once, checks verified email, a shape-valid immutable provider
user ID, and absence of impersonation. Identity is taken only from that exchange,
never by email matching or a registration Action ID. The ID and tokens are not
written to disk, receipts, cookies or application storage. State/verifier and
terminal outcomes are process-local; this is not durable admission/provisioning.

`signup_correlated` means **signup intent → verified provider subject**, not
proof that WorkOS created a new identity. Actual creation and required email
verification must be observed by the operator on the hosted flow. An existing
identity can follow a sign-up hint; ordinary sign-in is separately reported as
`verified_login_only` and never treated as new admission.

The installed SDK's public PKCE client omits an API key, including inherited
`WORKOS_API_KEY`. The harness fixes the API origin, timeout and zero retries;
neither a privileged provider credential nor a public Action endpoint is needed.
Only an isolated application's public Client ID is entered in the operator's
own visible terminal, using a hidden prompt. The computer-use workflow leaves
authentication settings and interactive account authentication to the operator.

Authoritative protocol references: [authorization/state/PKCE](https://workos.com/docs/reference/authkit/authentication/get-authorization-url),
[verified email](https://workos.com/docs/authkit/email-verification), and
[environment isolation](https://workos.com/docs/authkit/environments).
The [registration Action payload](https://raw.githubusercontent.com/workos/workos-node/main/src/actions/interfaces/action.interface.ts)
does not supply a final user ID or this application's OAuth state. This proof
does not claim to correlate pre-provisioning Actions or to cap provider identities.

## Operator launch — only after redirect readback

1. Verify the dashboard is **CareerTrellis Auth Rehearsal → Staging**, not
   **Job Applier → Staging**. Verify the exact callback above is saved in the
   rehearsal application's Redirect URIs. Leave other configuration untouched.
2. Have the rehearsal application's **Client ID**, not API key, available.
   Do not send it, passwords, email addresses, provider IDs, codes, tokens or
   callback URLs through chat, tool inputs, screenshots or environment variables.
3. In your own already-visible PowerShell terminal, run:

   ```powershell
   Set-Location 'C:\Users\hunte\Documents\Antigravity\jobapplier\.worktrees\beta-factor-readiness\backend'
   & 'C:\Users\hunte\Documents\Antigravity\jobapplier\backend\venv\Scripts\python.exe' -m signup_prototype.workos_rehearsal
   ```

4. Type `ISOLATED STAGING` when asked, then enter the rehearsal public Client ID
   at its hidden prompt. The helper refuses redirected/noninteractive input.
   It does not load the application's environment files or database.
5. Open `http://127.0.0.1:8765/` yourself. Confirm the page says **Isolated WorkOS
   rehearsal** and that it does not create a CareerTrellis account. The server
   binds only to loopback, accepts the exact Host, and stops after 30 minutes or
   Ctrl+C. HTTP cookies are a loopback development exception, not hosted policy.
6. Use a fresh, operator-controlled test identity and complete hosted creation
   and email verification yourself. A plus alias may be useful if your mailbox
   supports one. Do not submit someone else's address. Reserved example-domain
   addresses do not receive email, so they cannot demonstrate delivered email
   verification without a separate provider-supported test mechanism.

No assistant-started background secret prompt is used. A future production
callback must use the application's reviewed HTTPS and session controls.

## Live matrix and sanitized receipts

Use the harness landing page's buttons and its **Continue to isolated AuthKit**
link. It deliberately avoids cross-origin form redirects while retaining
`form-action 'self'`. The non-sensitive landing form uses `Referrer-Policy:
same-origin` so native browser POSTs preserve the exact Origin required by the
CSRF guard; callback, result, other responses and the provider link retain
no-referrer protection. Callback handling sends
an immediate redirect to the clean `/result` path. HTTP access/debug logs are
disabled in the operator launch, and exceptions use fixed classifications.

| Row | Required observation | Expected result |
|---|---|---|
| Fresh signup | Operator observes new account creation and required email verification; same browser returns before 10-minute expiry | `signup_correlated` |
| Existing identity via explicit signup | No assumption of new provider account; original state/verifier still binds | `signup_correlated`, separately labeled existing identity |
| Ordinary login control | Start with the login-only button | `verified_login_only` |
| Expiry | Use 30-second button and finish after expiry | `expired`, never successful correlation |
| Cross-browser/missing context | Open clean `/result` in a second browser context; offline tests exercise swapped callback/state without exposing codes | `no_browser_context`; a real swapped-callback run remains a separate live row |
| Duplicate callback | Offline tests prove one exchange and unchanged terminal outcome; do not paste a live callback URL into tools | Live row pending unless exercised by a privacy-safe harness extension |
| Cancellation | Cancel at provider if the hosted flow supplies a callback error | `provider_cancelled`; closing a tab is abandonment, not an observed error callback |
| Google | If intended for beta, repeat with separately approved test Google identity | Separate live evidence required; password success does not cover it |

Share only the fixed `/result` JSON, the row label, and yes/no observations of
new account creation and verification. The JSON always states zero application
accounts created and no retained provider tokens. It is not an application-cap
receipt or a durable account activation receipt.

If exchange returns `exchange_unavailable`, do not assume either provider
success or failure and do not retry that code. Start a genuinely new attempt
after diagnosing the fixed classification; no raw exception logging is allowed.
Expired attempts may leave an unused WorkOS identity. Do not delete users or
revoke settings/credentials merely as cleanup without exact-target approval.

## Offline acceptance / remaining gates

`test_workos_rehearsal.py` uses synthetic responses and no provider calls. It
covers browser/state rejection, strict verified/non-impersonated identity,
expiry before and during exchange, concurrent duplicate callbacks, preserved
terminal success after a late provider error, login-only intent, no-store and
clean callback results, Host/origin/CSRF/body limits, and public SDK credential
and destination isolation. This is not a full security review.

Offline receipt on 2026-09-07: **399 tests passed with two existing platform
skips** in 14.992 seconds (18 harness tests, 11 account-choice tests, 370 P0/object
tests). Compileall and whitespace checks passed; nothing is staged. The two
skips are the POSIX hosted-authority shell and Linux systemd checks on Windows.
Independent source acceptance identified and then confirmed correction of the
cross-origin form redirect and terminal-result overwrite issues. No browser or
live-provider execution is claimed by this receipt; callback setup was later
verified as recorded above, and the operator-run result was reported later. No server or secret prompt was started by
the assistant. No app deployment, push, PR or workflow was performed.

### Native form 403 correction

The first operator attempt was rejected locally, before WorkOS. A fresh
headless Edge context reproduced the cause: a native form POST from the former
`no-referrer` landing page carried `Origin: null`; the strict Origin check
returned 403. Earlier HTTP tests explicitly supplied Origin and missed this
browser behavior. The correction applies `same-origin` only to successful GET
responses for the non-sensitive landing page. It does not accept null/missing
Origin, relax browser/CSRF binding, change provider settings, or expose callback
queries. Fixed, actionable 403 responses now distinguish missing browser state,
rejected Origin and invalid form state without printing request values.

The browser regression routes every request into the isolated ASGI test client,
using synthetic configuration, and blocks provider calls. It proves the legacy
policy fails and the current policy reaches the normal Continue link. It does
not touch the operator's running harness, browser profile or WorkOS environment.
After updating source, the operator must stop the old process with Ctrl+C,
rerun the launch command, re-enter the hidden Client ID, and open a fresh home
page. Resubmitting the old `/start` page is not a valid retry.

Correction receipt: **402 tests passed, two existing platform skips**, in
12.379 seconds, including 21 harness tests and the native Edge legacy/fixed
comparison. Compileall and whitespace checks passed. The subsequent operator
report is recorded separately below.

### Operator-reported signup correlation — 2026-09-07

The operator reported that **Start signup proof** completed with:

```json
{"scope":"isolated_workos_protocol_only","status":"signup_correlated","application_accounts_created":0,"provider_tokens_retained":false}
```

This is operator-reported live evidence, not an independently observed browser
run. It records successful explicit signup-intent correlation to a verified
provider subject under the harness contract. Zero application accounts is the
expected isolated result, not a provisioning failure. The receipt reports no
retained provider tokens; no identities, codes or tokens were supplied.

The operator clarified that they logged in with their existing WorkOS account.
Classify this receipt as **existing identity via explicit signup**, not fresh
signup or the separate login-only control. New account creation and a required
email-verification interaction were not demonstrated by this first run. Later
attempts are recorded separately below; no application admission,
account-cap, MFA/recovery, deployment or broader-beta gate is closed by this
receipt.

### Operator-reported new-test-account correlation — 2026-09-07

In a subsequent attempt, the operator reported **With new test email account**
and supplied the same sanitized result:

```json
{"scope":"isolated_workos_protocol_only","status":"signup_correlated","application_accounts_created":0,"provider_tokens_retained":false}
```

Record this separately from the existing-account attempt as operator-reported
new-test-account correlation. The harness accepted a verified provider subject,
and the operator subsequently confirmed **It required email verification**.
Mark the fresh-signup matrix row passed on operator-observed evidence: new test
account, required email verification and successful correlated return. This is
not an independently observed browser run. Zero application accounts remains
expected, and this receipt does not close the remaining live controls or
application activation gates.

### Operator-reported login-only control — 2026-09-07

Using the newly created test account, the operator reported:

```json
{"scope":"isolated_workos_protocol_only","status":"verified_login_only","application_accounts_created":0,"provider_tokens_retained":false}
```

Mark the ordinary login control passed on operator-reported live evidence.
The harness classified this attempt as login-only rather than signup, with
zero application accounts created and no retained provider tokens reported.
This does not demonstrate a CareerTrellis session or application admission.
The subsequent expiry control is recorded below; other unrecorded live controls
remain open.

### Operator-reported expiry control — 2026-09-07

Following instructions to use the 30-second expiry proof and wait at least
40 seconds before continuing to AuthKit, the operator reported:

```json
{"scope":"isolated_workos_protocol_only","status":"expired","application_accounts_created":0,"provider_tokens_retained":false}
```

Mark the expiry control passed on operator-reported live evidence: the attempt
returned `expired`, not successful signup or login correlation. Elapsed time
was not independently measured. Zero application accounts created and no
retained provider tokens were reported. The subsequent missing-browser-context
check is recorded below; application activation is not established.

### Operator-reported missing-browser-context check — 2026-09-07

Following instructions to open the clean `/result` URL directly in a separate
browser or fresh private session that had not visited the rehearsal, the
operator reported:

```json
{"scope":"isolated_workos_protocol_only","status":"no_browser_context","application_accounts_created":0,"provider_tokens_retained":false}
```

Mark the clean-result missing-browser-context check passed on operator-reported
live evidence. This demonstrates that the result endpoint did not return an
authenticated flow outcome without its browser context. It does not exercise
a real callback with swapped browser/state, nor establish an application
session boundary. Never transfer a live callback URL or code to demonstrate it.

### Evidence boundary after the operator-run checks

Recorded live evidence now covers existing identity via explicit signup,
fresh-account signup with required email verification, login-only intent,
expiry, and missing context on the clean result endpoint. All live receipts
are operator-reported, with zero application accounts and no retained provider
tokens reported. No application deployment or publication has occurred.

Swapped callback/state and duplicate callback behavior have offline coverage,
not live acceptance. Provider cancellation remains unobserved; Google requires
separate approval and evidence if selected for beta. These rows are not silently
waived or marked passed. The next engineering evaluation is the bounded adapter
joining the verified callback subject to PostgreSQL reservation activation,
including retry, expiry, lifecycle and cap behavior. That integration is not
implemented or authorized for deployment by these protocol receipts. The
subsequent bounded evaluation and proposed acceptance scope are recorded in
`BETA_SIGNUP_INTEGRATION_EVALUATION.md`; it is not an integrated live receipt.

After the live protocol rows pass, real PostgreSQL reservation activation,
erasure/operator lifecycle integration, finite state retention, account-cap UI,
ingress changes, full MFA/recovery, production environment identity transition,
and deployment/live verification remain separate gates. The previous local
PostgreSQL proof remains documented in `BETA_SIGNUP_PROTOTYPE.md`.
