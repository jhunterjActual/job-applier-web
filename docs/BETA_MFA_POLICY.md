# Broader-beta authentication-factor policy

CareerTrellis is operated by **J. Hunter Johnson LLC**. This policy records the
factor decision for the broader beta; it does not change the current WorkOS
environment merely by being merged or deployed.

## Decision

Broader-beta access requires WorkOS AuthKit MFA. Passkeys are the preferred
phishing-resistant sign-in method and a verified passkey satisfies the MFA
requirement. Password sign-in remains available only with TOTP as a second
factor so users who cannot use platform biometrics or a hardware-backed passkey
retain an accessible route.

The current bounded friends-and-family pilot may remain password-only while its
invitation-only, verified-email, immutable preseeded-identity, anti-
impersonation, and disabled-user fail-closed controls remain in force. No new
broader-beta invitation may rely on that exception.

WorkOS documents that AuthKit can require TOTP enrollment for new and existing
users through the environment's Authentication settings:
<https://workos.com/docs/authkit/mfa>. AuthKit also supports passkeys and treats
a verified passkey as satisfying required MFA, but recommends configuring the
custom AuthKit domain first because enrolled passkeys are bound to that domain:
<https://workos.com/docs/authkit/passkeys/passkey-configuration/multi-factor-auth>.

## Activation sequence

1. Finalize and verify the CareerTrellis custom AuthKit domain before any
   passkey enrollment. Keep the existing application callback and logout
   origins exact and HTTPS-only.
2. Enable passkeys with progressive enrollment in the WorkOS staging
   environment.
3. Require MFA in that environment. Existing password users must be prompted
   to enroll TOTP; a user who signs in with a verified passkey must not receive
   a redundant TOTP challenge.
4. Keep password plus TOTP available as the non-passkey fallback. Do not enable
   SMS as a recovery shortcut.
5. Document one operator-assisted lost-factor recovery path that verifies the
   account holder outside the affected session, removes only the selected
   factor through WorkOS, revokes existing sessions, and requires fresh
   enrollment at the next sign-in. WorkOS exposes factor deletion for this
   purpose: <https://workos.com/docs/reference/mfa/factor>.

## Privacy-safe acceptance matrix

Use synthetic staging identities and retain only pass/fail, UTC time, tested
flow, environment, and reviewer. Never retain an email address, WorkOS ID,
factor ID, QR code, TOTP secret or code, passkey credential data, cookie,
authorization code, access token, refresh token, or recovery evidence.

- A new password user is required to enroll TOTP before the first completed
  broader-beta sign-in.
- An existing password user is required to enroll on the first sign-in after
  enforcement.
- A passkey-capable user can enroll on the final custom domain and sign in with
  the passkey without a redundant TOTP prompt.
- A user who declines or cannot use a passkey can complete password plus TOTP
  with keyboard-only navigation, visible focus, clear errors, and no timed
  enrollment step imposed by CareerTrellis.
- A wrong or expired TOTP and an unrecognized passkey fail closed without
  creating a CareerTrellis session.
- The operator-assisted lost-factor procedure removes only the intended
  synthetic factor, revokes prior sessions, and forces fresh enrollment.
- A disabled or unlisted WorkOS identity still fails CareerTrellis's local
  authorization boundary after otherwise valid factor authentication.
- Disabling the new factor policy restores the preceding sign-in route without
  changing CareerTrellis user ownership, re-enabling a disabled user, or
  weakening invitation-only registration. Previously enrolled factors are not
  silently deleted as part of rollback.

The factor gate is complete only after the staging configuration and every
applicable row above pass on the final custom domain. Production configuration
remains a separate future authorization.
