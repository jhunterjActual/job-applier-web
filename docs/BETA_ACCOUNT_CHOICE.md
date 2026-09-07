# Logout and account-choice acceptance

This bounded application correction supports the consecutive-user acceptance
work before broader beta. It does not enable MFA/passkeys, change WorkOS
settings, expand invitations, or clear the separate beta authentication and
backup-health gates. CareerTrellis is owned by **J. Hunter Johnson LLC**.

## Behavior

- A successful logout still uses the WorkOS session logout endpoint and the
  existing exact configured root return URI. With both local authentication
  cookies removed, root redirects to the no-store `/auth/sign-in` page. That
  page never begins authentication automatically.
- Logout deletes the session and pending PKCE-flow cookies, including after
  refresh. It cannot reissue the refreshed session on its response. An already
  authenticated but locally disabled/unlisted identity may terminate its own
  provider session without accessing any application data.
- An unverified/missing session or logout-URL failure clears both local cookies
  but returns a fixed, no-store 503 page explaining that provider sign-out is
  unconfirmed. It does not claim remote revocation or silently restart login.
  A provider failure after browser redirection is outside this local response;
  operators must verify the remote session is terminated during live acceptance.
- The sign-in page offers **Continue to sign in** and **Use a different
  account**, works without JavaScript, and retains maintenance notices.
- Each explicit sign-in starts fresh SDK-generated PKCE/state without an email
  or prior-identity hint and clears the previous local session. Callback
  verification, verified-email/anti-impersonation checks, and immutable local
  authorization are unchanged. Provider URL creation failure also removes the
  stale pending flow. A late callback with no matching flow cannot sign in.

## Provider contract and limits

Both choices use `provider=authkit`, `prompt=login`, and `screen_hint=sign-in`.
The different-account choice adds only the fixed upstream parameter
`provider_query_params[prompt]=select_account`; caller-supplied provider,
prompt, destination, and identity hints are never forwarded. It does not switch
to a direct GoogleOAuth flow or bypass AuthKit factor requirements.

The [WorkOS authorization API](https://workos.com/docs/reference/authkit/authentication/get-authorization-url)
exposes provider query parameters. The pinned Python SDK stringifies nested
dictionary values incorrectly, so this implementation appends this one fixed option
to its otherwise unchanged PKCE URL using the bracket encoding in the
[official WorkOS Node query serializer](https://github.com/workos/workos-node/blob/main/src/common/utils/query-string.ts).
No dependency upgrade or custom PKCE generation is introduced.
Local tests prove the emitted request, **not** that
the hosted AuthKit UI forwards it or that Google presents an account picker.
That behavior requires live acceptance. If a future run lacks the picker, reopen
the item and investigate the supported provider configuration; do not invent a new
prompt parameter or bypass AuthKit. The page supplies a separate browser
profile/private-window fallback and makes clear that CareerTrellis logout does
not sign out Google or other upstream providers.

## Accepted deployment

Build `20260906.70`, commit `ebf372f7988a9b36e90969710ee60a0914d1272b`,
completed activation on 2026-09-06 at 23:23:34 UTC. Post-deployment verification
at 23:23:54 UTC passed exact tracked bytes, application/worker readiness and
matching heartbeats, no-store landing, fixed PKCE account-choice requests,
two-cookie cleanup on the cookieless warning path, and cross-site rejection.
Caddy v7 passed 38 retained records, including nine query-bearing records;
both backup-health surfaces were green. The disabled announcement and all five
audit events were unchanged.

The operator subsequently replied **Verified** to the requested live check:
sign out, remain on the sign-in choices page, and use a different account,
including Google's account picker where applicable. This is operator-reported
acceptance of that bounded workflow, not an independently captured provider
session audit or proof that every possible provider/device combination passed.
No identities, session material, or provider responses were retained.

Native preparation passed 647 tests (one platform skip) and a separate 25-test
account-choice/runtime/browser/native-permission suite with no skips. The
failure, disabled/unlisted authorization, tenant-context, keyboard, reflow and
no-JavaScript cases have synthetic coverage; no live provider outage or
credential change was induced. MFA/passkey enrollment and broader-beta recovery
acceptance remain separate unfinished gates.

The operator explicitly approved publishing this documentation-only acceptance
closeout without another deployment. Application/runtime code is unchanged.

## Repeatable acceptance matrix

For future changes, repeat the applicable matrix with synthetic staging
identities in one normal browser profile. Retain only build/commit, UTC time,
fixed flow name and verdict;
never retain identities, email, authorization URLs, codes, cookies or tokens.

1. Sign in as synthetic user A, then sign out. Verify WorkOS termination, a
   stationary local sign-in page, no stale dashboard access on reload, and no
   automatic restoration of A. Check the root redirect and landing are no-store.
2. Use the alternate-account action and sign in as B. Verify B's own workspace,
   not A's. Repeat B-to-A. Exercise password and Google selection where enabled;
   verify Google's picker appears rather than silently selecting its prior user.
3. Confirm disabled/unlisted identities still cannot enter the application,
   even after otherwise successful provider authentication. Confirm a disabled
   existing session can sign out without gaining data access.
4. Verify the explicit local-only sign-out warning in synthetic failure tests;
   do not induce a provider outage or alter live credentials just to test it.
5. Verify keyboard navigation, 320px phone and tablet layouts, announcement
   display, no automatic navigation, and the no-JavaScript sign-in actions.

No database migration, provider-setting edit, or logout-return allowlist change
is required. The post-cutover `postgres-r2-authoritative` boundary disallows
ordinary code rollback, including a manual switch back to `.69`. Failed
activation invokes containment and requires a reviewed forward correction.
Recovery must not restore deleted browser cookies or alter WorkOS users/factors.
