# Two-Step Verification (Authenticator App and Passkeys) — Design

**Date:** 2026-10-11
**Status:** Approved (design)
**Area:** backend (new two-step service, sign-in, registry auth, backups, one migration),
frontend (sign-in page, two settings tabs, users page)

## Problem

A MegooCI account is protected by its password alone. The server holds deploy secrets, git
credentials and a container registry, so one leaked or guessed password is enough to read
all of them, change pipelines and push images. There is no way for a user to add a second
proof of who they are, and no way for an administrator to insist that everyone does.

## Goals

- Signing in always starts with the password. An account that has two-step verification
  then proves itself a second time, with either a code from an authenticator app or a
  passkey.
- Each user can turn it on for themselves, with one authenticator app and any number of
  passkeys.
- A user who has lost the device signs in with a one-time recovery code. An administrator
  can reset a user who has lost everything, and a server command can reset an
  administrator.
- An administrator can require two-step verification for every account.
- The account password alone no longer works for `docker login` once an account uses
  two-step verification; an API token does.
- Backups carry two-step verification with them, and a restore cannot lock out the
  administrator who runs it.

## Non-goals

- Signing in with a passkey instead of the password. The password is always asked.
- Codes by SMS or email.
- Remembering a browser so that it skips the second step.
- Telling the user by email or in-app notification that a method was removed or a recovery
  code was used. The audit log records it.
- Requiring two-step verification by role. The requirement is for everyone or no one.
- Changing how long a session lasts. Sessions stay as they are.
- A second step for API tokens, MCP, agents or deploy tokens. They are separate
  credentials, created inside a verified session.

## Terms

- **Method** — a confirmed authenticator app, or a passkey.
- **Two-step verification is on** for an account that has at least one method.
- **Required** — the administrator's switch is on.
- **Ticket** — a random, short-lived value the server hands out after the password is
  accepted. It proves "this browser gave the right password for this account" and nothing
  else. It cannot be used as a session.

The feature is called "Two-step verification" everywhere in the interface.

## Signing in

### The password step

`POST /api/v1/auth/login` checks the email and password exactly as today, including its
rate limit and its audit entries for failures. What it returns then depends on the account:

| The account | Response `status` | Carries |
| --- | --- | --- |
| has no method, and it is not required | `signed_in` | the session tokens, as today |
| has a method | `two_step_required` | a ticket (5 minutes), the methods it has |
| has no method, and it is required | `two_step_setup_required` | a setup ticket (15 minutes) |

All three are HTTP 200. `signed_in` keeps the `access_token`, `refresh_token` and
`token_type` fields where they are today, so a script that signs in an account without
two-step verification keeps working. `login_success` is written to the audit log when the
session is issued, not when the password is accepted.

### The second step

One screen, showing only what the account has: a field for the 6-digit code, a "Use a
passkey" button, or both. An account with passkeys only gets the browser's passkey prompt
at once.

- **Code:** `POST /api/v1/auth/two-step/verify` with the ticket and the code.
- **Passkey:** `POST /api/v1/auth/two-step/passkey/options` with the ticket returns a
  challenge; the browser signs it; `POST /api/v1/auth/two-step/passkey/verify` checks the
  answer.
- **Recovery code:** `POST /api/v1/auth/two-step/verify` with the ticket and a recovery
  code.

A correct answer returns the session and uses the ticket up. A recovery code works once;
the response says how many are left, and the page tells the user after signing in.

A code is accepted for the current 30-second step and the one on either side of it, to
allow for clocks that are slightly off. The server remembers the last step it accepted for
the account and refuses that step and any earlier one, so a code cannot be used twice.

### Wrong tries

Wrong codes, wrong recovery codes and failed passkey answers are counted per account in
the database. Five in a row pause the second step for that account for five minutes: every
attempt in that time is refused with "Too many wrong tries. Try again in N minutes."
without being checked. A correct answer, or the end of the pause, resets the count.

The count is kept in the database and not in Redis because the existing rate limiter lets
every request through when Redis cannot be reached. Reaching the second step at all needs
the account's password, so this cannot be used to lock someone else out.

### Forced setup

With `two_step_setup_required`, the sign-in page shows "Set up two-step verification —
Your administrator requires it for every account" and the two choices. The setup ticket
opens only these endpoints:

- `POST /auth/two-step/setup/totp` — a new secret and its `otpauth://` address.
- `POST /auth/two-step/setup/totp/confirm` — a code from the app; on success the
  authenticator is the account's method and the recovery codes are returned.
- `POST /auth/two-step/setup/passkey/options`, `POST /auth/two-step/setup/passkey` — the
  same for a passkey, with a name.
- `POST /auth/two-step/setup/finish` — returns the session. Refused unless the account now
  has a method.

A setup ticket adds one method. Once the account has it, the ticket opens only `finish`;
further methods are added from Settings.

The page shows the recovery codes and enables "Continue to MegooCI" only once "I have
saved these codes" is ticked; that button calls `finish`. Someone who closes the page
after confirming but before finishing has two-step verification on and signs in with it
next time; new recovery codes can be generated in Settings.

### Sign-up and invitations

`POST /auth/signup` and accepting an invitation also issue a session today. Both go
through the same decision as the password step, so on a server that requires two-step
verification a new account receives a setup ticket and lands on the forced setup.

One function decides, for a user who has just proved their password: session, second
step, or setup. The four places that issue tokens today (sign-up, login, refresh,
invitation) stop building tokens themselves.

### Sessions that are already open when it becomes required

A session renews itself every time it is used, so "at the next sign-in" could be never.
When the requirement is on and an account has no method:

- **At once:** `GET /auth/me` reports `two_step_setup_required: true`, and the app shows a
  banner on every page: "Your administrator requires two-step verification." with a button
  to the Settings tab. Setting it up there needs no sign-out.
- **Within 12 hours:** `POST /auth/refresh` is refused for that account. Its access token
  lasts at most 12 hours (the existing lifetime), after which the browser is sent to the
  sign-in page and the forced setup.

Someone who ignores the banner can be signed out in the middle of work, once.

API tokens are not sessions. An account that has not set two-step verification up keeps
the use of its API tokens while it is required.

## Managing methods

A new tab, **Settings → Account → Two-step verification**, for every user. It shows
whether it is on, whether it is required, and three cards.

**Authenticator app.** "Set up" opens a dialog with a QR code, the same secret as text for
typing, and a field for the code. Nothing is turned on until a correct code proves the app
has the secret. One authenticator per account; to move to a new phone, remove it and set
it up again.

**Passkeys.** "Add a passkey" asks the server for a challenge, opens the browser's own
prompt, then asks for a name. Each passkey is listed with its name, when it was added and
when it was last used, and can be renamed or removed. At most ten per account.

**Recovery codes.** Shows how many of the ten are left and when they were generated.
"Generate new codes" replaces all of them. The codes themselves are shown only at the
moment they are generated, with Copy and Download.

Rules:

- Ten recovery codes are generated when an account's first method is added, and shown
  then.
- Removing a method, and generating new codes, ask for the account's password.
- Removing the last method turns two-step verification off and deletes the recovery codes.
  It is refused while two-step verification is required.
- These endpoints need a browser session. A request authenticated with an API token is
  refused, whatever its scope.
- Existing sessions are not ended when a method is added or removed.
- A password reset by email changes the password only. The next sign-in still asks for the
  second step.

## Administrators

**The switch.** A new tab, **Settings → Server → Security**, for administrators: "Require
two-step verification", with a count of how many users have it on. It can be turned on
only by an administrator whose own account has it on, so nobody requires it and then finds
they cannot get in.

**The users page.** A "Two-step" column (On / Off), and "Reset two-step verification" in
each row's menu. After a confirmation it removes that user's authenticator, passkeys,
recovery codes and any pause from wrong tries. The user's open sessions are left alone. If
it is required, that user sets it up again at the next sign-in.

**A locked-out administrator.** `python -m app.reset_two_step <email>` on the server (in
the backend container) does what the reset does, for when no other administrator is left
to do it.

## The registry (`docker login`)

The registry's token endpoint accepts a username and password. Today the password is the
account password or, for the username `deploy-token`, a deploy token.

- **A deploy token** — unchanged.
- **An API token as the password** — new, for any account. The token says who it is; the
  username is not checked. What it may do is what the token's owner may do, narrowed by
  the token's scope, as for any other API call.
- **The account password** — checked as today. If it is right and the account has
  two-step verification on, or it is required and the account has not set it up, the
  request is refused with: "This account uses two-step verification. Use an API token as
  the password (Settings → API tokens)." The message is only given for a correct password,
  so it does not reveal which accounts use two-step verification.

The refusal is sent in the registry error format, which the docker client prints.

## Passkeys in detail

- The relying party is the host of `MEGOOCI_PUBLIC_URL`, and the only accepted origin is
  that URL's origin. A passkey therefore works only when the app is opened at that
  address, and browsers allow passkeys only over HTTPS or on `localhost`.
- The server sends its passkey origin with the sign-in and settings responses. When the
  page is open at any other address, or the browser has no passkey support, "Add a
  passkey" and "Use a passkey" are replaced by a line saying where passkeys work. An
  account with passkeys only can still use a recovery code there.
- A passkey is a second step after the password, so it does not have to verify the user
  itself: user verification is "preferred", which lets a plain security key without a PIN
  be used. No attestation is requested.
- A passkey already registered to the account cannot be registered again.
- If an authenticator reports a signature counter that has gone backwards, the answer is
  refused and the event is recorded: it is the sign of a copied key.
- Each challenge is good for one answer. Asking for a new challenge replaces the last, and
  checking an answer discards it, right or wrong.
- If `MEGOOCI_PUBLIC_URL` later moves to another host, existing passkeys stop working.
  Authenticator codes and recovery codes are not affected.

## Data

One migration adds four tables.

**`user_two_step`** — one row per user who has started with two-step verification.

| Column | Holds |
| --- | --- |
| `user_id` | primary key, references `users`, deleted with the user |
| `totp_secret_encrypted` | the authenticator secret, encrypted with `MEGOOCI_SECRET_KEY` like other stored secrets; empty when there is no authenticator |
| `totp_confirmed_at` | when a correct code confirmed it; empty while setup is unfinished |
| `totp_last_step` | the last 30-second step a code was accepted for |
| `totp_last_used_at` | when a code was last accepted |
| `recovery_codes_generated_at` | when the current recovery codes were made |
| `failed_attempts`, `locked_until` | the wrong-try count and the end of the pause |

**`user_passkeys`** — `id`, `user_id`, `credential_id` (unique), `public_key`,
`sign_count`, `transports`, `name`, `created_at`, `last_used_at`.

**`user_recovery_codes`** — `id`, `user_id`, `code_hash`, `used_at`, `created_at`. A code
is ten characters shown as `xxxxx-xxxxx`, from letters and digits without the ones that
look alike. Codes are hashed with the password hasher, so a copy of the database does not
give them away; checking one compares it with the account's unused codes off the event
loop.

**`two_step_tickets`** — `id`, `user_id`, `purpose` (`verify`, `setup` or `register`),
`token_hash`, `webauthn_challenge`, `expires_at`, `created_at`. The browser holds the
random token; the server stores its SHA-256. `register` tickets carry the challenge for
adding a passkey from Settings. Expired tickets are deleted whenever a ticket is created.

**The switch** is the row `require_two_step` in `system_settings`.

Authenticator codes are the standard kind every app supports: 6 digits, 30 seconds,
SHA-1, a 160-bit secret. The `otpauth://` address names the issuer "MegooCI" and the
account's email.

## API

Sign-in (public, each needs a ticket):

| Endpoint | What it does |
| --- | --- |
| `POST /auth/login`, `POST /auth/signup`, invitation accept | return one of the three `status` values |
| `POST /auth/two-step/verify` | ticket + `code` or `recovery_code` → session |
| `POST /auth/two-step/passkey/options`, `.../passkey/verify` | passkey as the second step |
| `POST /auth/two-step/setup/totp`, `.../totp/confirm` | forced setup, authenticator |
| `POST /auth/two-step/setup/passkey/options`, `.../setup/passkey` | forced setup, passkey |
| `POST /auth/two-step/setup/finish` | → session |

Own account (browser session only):

| Endpoint | What it does |
| --- | --- |
| `GET /auth/two-step` | on, required, the authenticator, the passkeys, recovery codes left, passkey origin |
| `POST /auth/two-step/totp`, `.../totp/confirm` | set up the authenticator |
| `POST /auth/two-step/totp/remove` | needs the password |
| `POST /auth/two-step/passkeys/options`, `POST /auth/two-step/passkeys` | add a passkey |
| `PATCH /auth/two-step/passkeys/{id}` | rename |
| `POST /auth/two-step/passkeys/{id}/remove` | needs the password |
| `POST /auth/two-step/recovery-codes` | needs the password; returns ten new codes |

`GET /auth/me` gains `two_step_enabled` and `two_step_setup_required`.

Administrators:

| Endpoint | What it does |
| --- | --- |
| `GET /system/security`, `PUT /system/security` | the switch, and how many users have it on |
| `POST /users/{id}/two-step/reset` | reset one user |
| `GET /users/...` | each user gains `two_step_enabled` |

## Backups

- `user_two_step`, `user_passkeys` and `user_recovery_codes` join the tables a backup
  holds, after `users`. The authenticator secret is listed as an encrypted column, so a
  backup carries it in the clear inside the passphrase-encrypted file and a server with
  another `MEGOOCI_SECRET_KEY` can restore it. `two_step_tickets` is not backed up.
- `require_two_step` is configuration: it is backed up and restored like other settings.
- The rule that the administrator who restores is never locked out is extended: that
  account keeps its current two-step verification (authenticator, passkeys, recovery
  codes), whatever the backup holds for it, as it already keeps its password.
- After a restore onto a server at another address, passkeys do not work there;
  authenticator codes and recovery codes do.
- The migration changes the schema revision, so by the existing rule a backup made before
  this release cannot be restored after it. Make a new backup after upgrading.

## Frontend

- **Sign-in page.** After the password, the store holds what is pending (the ticket, the
  methods, the account's email) in memory only. The page then shows the second step, the
  recovery-code screen or the forced setup in the same card. A reload forgets the ticket
  and starts again from the password. The sign-up and invitation pages hand over to the
  same screens when the server answers `two_step_setup_required`.
- **Shared pieces** used by both the sign-in card and the Settings dialogs: the
  authenticator setup (QR code, secret, code field), the recovery-code list (Copy,
  Download, "I have saved them"), and a small passkey helper around the browser API.
- **Settings.** Two new tabs: "Two-step verification" under Account, between Password and
  Appearance, for everyone; "Security" under Server, after Maintenance, for administrators.
- **Users page.** The column and the reset action.
- **App layout.** The banner when `two_step_setup_required` is true.

## Error handling

| Situation | What the user sees |
| --- | --- |
| Wrong code, recovery code or passkey answer | "That code is not right." / "That passkey was not accepted." — the ticket stays valid |
| Five wrong tries | "Too many wrong tries. Try again in N minutes." |
| Ticket expired or unknown | "That took too long. Sign in again." — back to the password |
| A code that was already used | treated as a wrong code |
| The browser's passkey prompt was cancelled | nothing is sent; the screen stays as it is |
| Passkeys not usable at this address | the passkey controls are replaced by a line saying where they work |
| Wrong password when removing or regenerating | "Wrong password." |
| Removing the last method while it is required | "Two-step verification is required on this server." |
| Turning the requirement on without having it oneself | "Set up two-step verification on your own account first." |
| Renewal refused because it is required and not set up | the sign-in page, then the forced setup |

Responses to the second step never say whether an account has a particular method beyond
what the password step already returned to that browser.

## Security

- A ticket is 32 random bytes, stored hashed, bound to one account and one purpose, and
  expires in minutes. It never authenticates an ordinary API request.
- A setup ticket is only issued to an account with no method. It cannot be used to add a
  method to an account that already has one.
- Authenticator secrets are encrypted at rest; recovery codes are hashed; a passkey's
  private key never reaches the server.
- Secrets, codes and tickets are never written to logs or to the audit log.
- The audit log records: a method added or removed, recovery codes generated, a recovery
  code used, a failed second step, a pause after five wrong tries, a passkey counter going
  backwards, an administrator's reset, and a change of the requirement.
- An administrator's reset is the one way around a user's second step, and it is limited
  to administrators and recorded. The server command needs shell access to the server.
- The existing per-address rate limit on sign-in also covers the new public endpoints and
  the own-account endpoints that ask for the password. Wrong codes typed while setting an
  authenticator up in Settings are not counted towards the pause: the secret being tested
  is the user's own.

## Testing

Backend, test-first, on the in-memory database the suite already uses:

- **Codes:** accepted in the current step and one either side; refused outside; refused
  twice; refused for an unconfirmed authenticator. The clock is passed in.
- **Recovery codes:** format, one use each, the count left, regeneration cancels the old
  ones, removed with the last method.
- **Passkeys:** a software authenticator in the tests creates real key pairs and signs
  real challenges, so registration and sign-in run through the library's checks: wrong
  origin, wrong challenge, a challenge used twice, an unknown credential, a counter going
  backwards, a credential registered twice.
- **Tickets:** expiry, purpose, single use, the wrong account, a setup ticket for an
  account that has a method.
- **Wrong tries:** the pause begins at five, refuses a correct code while it lasts, ends
  on time, and resets on success.
- **The sign-in decision:** each of the three outcomes, for login, sign-up and invitation;
  renewal refused only when required and not set up; `me` reporting both new fields.
- **Managing:** the password is needed where the design says; the last method cannot be
  removed while required; API tokens are refused.
- **Administrators:** the switch needs an administrator who has it on; reset clears
  everything and is audited; the server command.
- **Registry:** deploy tokens unchanged; an API token accepted and limited by its scope;
  the password refused only when it is right and two-step verification applies; the
  password accepted otherwise.
- **Backups:** the three tables round-trip, including to a server with another key; the
  restoring administrator keeps their own two-step verification.
- **Migration:** applied and reverted on a real PostgreSQL in a throwaway container.

Frontend: `npx tsc --noEmit` and `next build`. There is no frontend test harness; pure
helpers get a throwaway script, as before.

Not something the implementation can check, and to be tried by hand before relying on it:
a real authenticator app, a real fingerprint reader or security key, the screens in a
browser, and a real `docker login`.

## Files touched

Backend:

- `app/models/two_step.py` — the four tables. `alembic/versions/024_two_step_verification.py`.
- `app/services/two_step/` — `totp.py`, `recovery_codes.py`, `passkeys.py`, `tickets.py`,
  `attempts.py`, `status.py` (what an account has; whether it is required), `sign_in.py`
  (the one decision), `reset.py`.
- `app/api/v1/two_step.py` — the sign-in and own-account endpoints.
- `app/api/v1/auth.py`, `invites.py` — use the sign-in decision; `me`; renewal.
- `app/api/v1/system.py`, `users.py` — the switch; the column and reset.
- `app/core/registry_auth.py`, `app/api/v1/registry_oci.py` — API tokens, the refusal.
- `app/services/backup/tables.py`, `restore.py` — the tables; the restoring administrator.
- `app/reset_two_step.py` — the server command.
- `pyproject.toml` — `pyotp`, `webauthn`.

Frontend:

- `src/lib/api.ts`, `src/lib/auth.ts`, `src/lib/passkeys.ts`.
- `src/components/two-step/` — authenticator setup, recovery codes.
- `src/components/login/` — second step, forced setup; `src/app/login`, `signup`,
  `invite/accept`.
- `src/components/settings/two-step-tab.tsx`, `security-tab.tsx`; `src/app/settings/page.tsx`.
- `src/app/admin/users/page.tsx`, `src/components/layout/app-layout.tsx`.
- `package.json` — `@simplewebauthn/browser`, `qrcode`.

Docs: a "Two-step verification" section in `README.md`.

## Amendments found while planning

Writing and testing the code ahead of the plan changed or added these points. Where they
differ from the text above, they are what gets built.

1. **A password-reset link is a session today.** The existing code builds the link's token
   as an access token, so whoever reads the email is signed in for an hour, and the reset
   endpoint itself has never accepted the token. Left alone, that link would walk past the
   second step. The plan fixes it first: the link's token becomes a `reset` token that sets a
   new password and nothing else.
2. **A new API token scope, "Container registry"** (pull and push). No existing scope
   allows a push, so without it an account with two-step verification would need a
   Full-access token for `docker login`. A scoped token that does not cover the registry is
   refused with "This API token's scope does not include the registry." No scope allows
   deleting images.
3. **An administrator cannot reset their own two-step verification from the Users page.**
   In Settings the same change asks for the password, and the reset must not be a way
   around that.
4. **Status codes on the sign-in routes:** a wrong answer is 401, a ticket that is unknown,
   expired or used is 410, a pause is 429. The page goes back to the password on 410.
5. **A setup ticket turns into a `setup_done` ticket** once it has added its method, and
   only that opens `/setup/finish`. A setup ticket is refused outright once the account has
   a method that was not added with it, so a ticket held by someone who has only the
   password is worthless as soon as the owner sets two-step verification up.
6. **Codes typed with digits of another script** (an Arabic or Persian keyboard) are read
   as their digits.
7. **The forced setup asks for a passkey's name** after the browser has created it, as
   Settings does.
8. **The Users page is a list of rows, not a table,** so "Two-step" is a badge beside each
   name rather than a column.
9. **Modules the file list above does not name:** `state.py` (the per-user row), `clock.py`
   (the time, in one place), `two_step_account.py` (the own-account routes, apart from the
   sign-in routes in `two_step.py`), and `password-dialog.tsx`.
10. **The refusal for `docker login` was tried against a real docker client** and a stub
    registry: it prints `unauthorized:` followed by the message.

## Rollback

Turn the requirement off and nobody is asked to set it up. To remove the feature, revert
the release and run the migration's downgrade, which drops the four tables; accounts go
back to password-only sign-in. A backup made on this release cannot be restored on the
older one.
