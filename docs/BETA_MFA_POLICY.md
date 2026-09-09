# Broader-beta authentication-factor policy

CareerTrellis is operated by **J. Hunter Johnson LLC**. This policy records the
factor decision for the broader beta; it does not change the current WorkOS
environment merely by being merged or deployed.

## Decision

Broader-beta access requires MFA. WorkOS AuthKit without paid add-ons is the
conditional provider candidate pending the self-service admission and provider
rehearsals in `BETA_SIGNUP_PROTOTYPE.md`; it is not activation approval.
Passkeys are the preferred
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
<https://workos.com/docs/authkit/passkeys>.

## Environment and domain prerequisite — revised, activation unresolved

Readiness review on 2026-09-06 found that the original instruction to configure
a custom AuthKit domain in **WorkOS staging** is not supported by the documented
provider offering. [WorkOS custom domains](https://workos.com/docs/custom-domains/authkit)
are production-only. The application's `staging-app` hostname does not establish
the WorkOS environment type. Verify the actual provider environment separately.
`backend/web_auth.py` independently rejects the application's `production`
runtime. Do not remove that gate or relabel the application environment as an
incidental part of provider setup.

[Custom domains are a paid service](https://workos.com/docs/custom-domains),
listed at **$99/month** on the [pricing page](https://workos.com/pricing) at this
review. The operator rejected that add-on for the no-revenue effort. A custom
domain is no longer a prerequisite; evaluate a stable provider-managed domain
without paid add-ons. Neither this
policy nor a request to continue beta work authorizes billing activation,
purchase, DNS changes, provider-environment replacement, or identity migration.

[WorkOS environments](https://workos.com/docs/authkit/environments) have separate
users and credentials; moving to production is not an automatic promotion of
staging users. CareerTrellis binds tenant access to immutable external IDs, not
matching email addresses. A credentials-only switch is therefore not an
acceptable migration. Before any switch, require a reviewed identity-preserving
transition design, synthetic rehearsal, explicit operator approval, and proof
that existing tenant data remains correctly owned and accessible. Never reuse
the pilot enrollment helper to silently rebind existing users or match by email.

The candidate path is a stable provider-managed production domain, subject to
an accepted future passkey re-enrollment strategy if the domain changes and
verification of the actual account's costs and available capabilities. Staging-
only tests cannot close the final-domain gate. The product destination is
self-service verified registration, capped during beta and uncapped at full
release, not permanent operator provisioning. See `BETA_SIGNUP_PROTOTYPE.md`.

Before activation, obtain approval for the exact environment, permanent AuthKit
hostname, any cost, identity transition, and factor-enforcement window. Keep the
existing pilot configuration unchanged while these prerequisites are unresolved.
Production activation remains separately authorized; broader-beta invitations
remain blocked by this factor gate and the separate recovery gates.

## Activation sequence

1. Resolve the environment/domain prerequisite above, then finalize and verify
   the accepted stable AuthKit domain before any
   passkey enrollment. Keep the existing application callback and logout
   origins exact and HTTPS-only.
2. Enable passkeys with progressive enrollment in the explicitly approved
   WorkOS environment only after the final domain is verified.
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

Use synthetic test identities in the approved environment and retain only
pass/fail, UTC time, tested
flow, environment, and reviewer. Never retain an email address, WorkOS ID,
factor ID, QR code, TOTP secret or code, passkey credential data, cookie,
authorization code, access token, refresh token, or recovery evidence.

- A new password user is required to enroll TOTP before the first completed
  broader-beta sign-in.
- An existing password user is required to enroll on the first sign-in after
  enforcement.
- A passkey-capable user can enroll on the final accepted domain and sign in with
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

The factor gate is complete only after the approved environment configuration
and every applicable row above pass on the final accepted domain. A provider
factor-policy reversal is not an application rollback or identity migration:
the post-cutover application authority still requires reviewed forward
correction. Production configuration remains a separate future authorization.
