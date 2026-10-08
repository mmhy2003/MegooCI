# Configuration Backup and Restore — Design

**Date:** 2026-10-08
**Status:** Approved (design)
**Area:** backend (new backup service, admin API, scheduled task), frontend (new admin page)

## Problem

Everything an administrator sets up in MegooCI — users and roles, projects, pipelines,
secrets, notification channels, git connections, agents — lives in one PostgreSQL database
on one server. There is no way to take a copy of it from the application, to move it to a
new server, or to put a server back the way it was yesterday. Secret values are encrypted
with a key that lives in the server's environment, so even a raw database dump is not
enough to rebuild a server elsewhere.

## Goals

- An administrator can create a backup of the server's **configuration**, download it,
  upload one, restore one, and delete one, from an admin page.
- Backups can be made automatically on a schedule, with a retention count.
- A backup is encrypted with a passphrase, and can be restored on a server that has a
  different `MEGOOCI_SECRET_KEY`.
- A restore makes the server's configuration exactly what the backup holds, keeps the build
  history of everything that still exists, and either completes or changes nothing.
- Optionally, each backup is also copied to S3-compatible storage the administrator
  configures.
- Only administrators can see or use any of it.

## Non-goals

- Backing up build history, logs, artifacts, registry images, the audit trail or delivery
  history. This is a configuration backup, not a copy of the server.
- Restoring a backup made by a different version of the database schema.
- Listing or restoring backups directly from the remote storage.
- Merging a backup into existing configuration, or restoring part of a backup.
- Remote storage other than the S3 API (SFTP, WebDAV, cloud-specific APIs).
- Changing how secrets are encrypted at rest on the server.

## What a backup contains

| Included | Left out |
|---|---|
| Users, roles, role assignments | Builds, stages, steps, log chunks |
| Projects, pipelines (with their YAML), triggers, webhook endpoints | Artifacts and their files; registry repositories, images, tags, events and their files |
| Secrets and environment variables | Audit trail; webhook and notification delivery history |
| Notification channels, git provider connections, project repositories | In-app notifications; pending invites |
| Agents, API tokens, registry deploy tokens | The search index and Redis |
| System settings | The backup settings (see below) |

- Password hashes and token hashes are included as they are, so users, agents and API
  tokens keep working after a restore.
- Values the server stores encrypted with `MEGOOCI_SECRET_KEY` — secret values,
  notification channel configuration, git connection credentials, webhook secrets — are
  decrypted when the backup is written and re-encrypted with the restoring server's key when
  it is restored. Inside the backup they are protected by the backup's own encryption.
- **Backup settings belong to the server, not to the configuration.** The passphrase, the
  schedule, the remote storage settings and the record of past runs are never written into a
  backup, and a restore leaves them untouched. A restore can therefore never break the next
  scheduled backup.
- **Maintenance mode is server state too.** Whether maintenance mode is on, and its message,
  are not in a backup and are not changed by a restore: a restore must not turn maintenance
  mode off under the administrator who just turned it on. They turn it off themselves when
  they have checked the result.

## The backup file

- One file per backup, named `megooci-backup-YYYYMMDD-HHMMSS-<kind>.mcbak` in UTC, where
  kind is `manual`, `scheduled`, `pre-restore` or `uploaded`.
- Files live in `<MEGOOCI_STORAGE_ROOT>/backups/`. That directory is the list of backups:
  a file copied into it by hand appears on the page, and nothing about a backup is stored in
  the database.
- The kind and time shown on the page come from the file name. An uploaded file is stored
  under a name built from the creation time in its own header and the kind `uploaded`, so
  the list shows when the backup was made, not when it was uploaded.
- The file has a short readable header followed by the encrypted body:
  - **Header** (not secret): format version, creation time, kind, the database schema
    revision, the number of rows per table, and the key-derivation parameters. It lets the
    page list a backup, and refuse an incompatible one, without the passphrase.
  - **Body**: the configuration as compressed JSON, encrypted with AES-256-GCM. The key is
    derived from the passphrase with scrypt and a random salt. The header is authenticated
    with the body, so neither can be altered without the restore failing.
- A configuration backup is small — typically well under ten megabytes — so it is built and
  read in memory.

### The passphrase

- The administrator sets one backup passphrase, at least 12 characters. It is stored
  encrypted with the server key and is never shown again.
- Every backup the server makes, manual or scheduled, uses it. No backup can be made until
  it is set.
- Changing it affects new backups only. Older files still need the passphrase they were
  made with.
- A restore tries the stored passphrase first. If the file was made with another one — an
  uploaded file, or a file older than a passphrase change — the page asks for it.
- The page says plainly that a backup cannot be restored without its passphrase, and that
  the passphrase is not recoverable from the server.

## Creating a backup

1. Check that a passphrase is set and that no other backup or restore is running.
2. Read the configuration tables in one database transaction, so the copy is consistent.
3. Decrypt the server-encrypted values, build the file, and write it to the backups
   directory under a temporary name, renaming it when complete. A half-written file is never
   listed.
4. If the remote copy is enabled, upload the file (see Remote copy).
5. Record the event in the audit trail.

A manual backup runs within the request and returns when the file is written. Builds are not
paused: reading the configuration does not block them.

## Restoring a backup

### Preconditions

All of these are checked before anything changes, and each failure is reported with what to
do about it:

- The user is an administrator.
- Maintenance mode is on, so no new build can start.
- No build is running.
- No other backup or restore is running.
- The file's header is readable, its format version is known, and its schema revision equals
  the server's.
- The passphrase opens the file.
- The request carries the confirmation word `restore`.

### Steps

1. Take a backup of the current configuration, kind `pre-restore`. If that fails, the
   restore does not start.
2. In **one database transaction**:
   - Cancel pending builds.
   - For each configuration table, delete the rows that are not in the backup, then insert
     or overwrite the rows that are, re-encrypting secret values with this server's key.
   - Mark every agent offline with no current build; agents reconnect on their own.
3. Commit. If any step fails, the transaction is rolled back and the server is exactly as it
   was.
4. After the commit: rebuild the search index, and record the restore in the audit trail.

### What happens to history

History is not in the backup, so a restore decides what to do with rows that point at
configuration:

- Build history of a pipeline that exists in the backup is kept, with its logs and
  artifacts.
- Build history of a pipeline the restore removes is removed with it, as it is when a
  pipeline is deleted today. The same holds for the registry repositories of a removed
  project and the delivery history of a removed channel or repository.
- Where history is still meaningful without the thing it points at, the reference is
  cleared instead: a build triggered by a removed user stays, with no user.

### The administrator who restores is never locked out

The account running the restore keeps its current password and stays an active
administrator, whatever the backup says. If the backup has that account (the same id, or the
same email), everything else about it comes from the backup; if not, the account is kept as
it is. Without this rule, restoring an old backup could leave nobody able to sign in.

### Known limitation: exchanged names

A restore rewrites rows one at a time. If two things that both exist in the backup have
exchanged a unique value since it was made — two projects swapped names, two users swapped
email addresses — the first rewrite collides with the other row, the restore fails, and
nothing is changed. Rename one of them and restore again. A name taken by something created
after the backup is not a problem: that row is on its way out and gives the name up first.

### Same schema version only

A backup can be restored only by a server whose database schema revision equals the
backup's. To restore a backup from an older release: install that release, restore, then
upgrade. The page shows the revision of each backup and marks incompatible ones.

## Schedule

- Settings: frequency (`off`, `daily`, `weekly`), time of day in UTC, weekday for weekly,
  and how many scheduled backups to keep (default 14, minimum 1).
- A periodic task checks every five minutes whether a scheduled backup is due and makes it.
  A backup that was due while the server was down is made once when it comes back, not once
  per missed slot.
- After each scheduled backup, scheduled backups beyond the retention count are deleted,
  oldest first — locally, and remotely when the remote copy is enabled. Manual, uploaded and
  pre-restore backups are never deleted automatically.
- A scheduled backup needs no maintenance mode and does not pause builds.
- The result of the last scheduled run (time, success, or the reason it failed) is shown on
  the page. A failure also sends an in-app notification to administrators.

## Remote copy (optional)

- Off by default. Settings: endpoint URL (empty for AWS), region, bucket, optional key
  prefix, access key id, secret access key. The secret is stored encrypted with the server
  key and is never returned by the API.
- **Test connection** writes and deletes a small object under the prefix and reports the
  storage's own error if it fails.
- When enabled, every backup the server makes — manual, scheduled, pre-restore — is uploaded
  after it is written locally, under the same file name.
- A failed upload does not fail the backup. The page shows, per backup, whether the remote
  copy exists, is missing or failed (with the reason), and offers **Retry upload**.
- Deleting a backup on the page deletes its remote copy as well, when the remote copy is
  enabled; a remote failure is reported and the local file is still deleted.
- Restoring uses local files. To restore a backup that exists only in the bucket, download
  it from the bucket and upload it on the page.
- The remote copy status of each file is kept with the backup settings (server state), not
  in the backup.

## Access and API

Every endpoint requires a global administrator. A project-scoped role — an admin role held
for one project included — or an API token without the admin scope, gets 403. The check is
stricter than the one the other admin pages use, which accepts an admin role in any scope.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/admin/backups` | List backups (name, kind, time, size, schema revision, compatible, remote status), the settings without their secrets, and the last scheduled run. |
| `POST /api/v1/admin/backups` | Create a manual backup. |
| `POST /api/v1/admin/backups/upload` | Upload a backup file (at most 100 MB; the header must be valid). It is stored with kind `uploaded`. |
| `GET /api/v1/admin/backups/{name}/download` | Download a backup file. |
| `DELETE /api/v1/admin/backups/{name}` | Delete a backup. |
| `GET /api/v1/admin/backups/{name}/restore-checks` | The preconditions of a restore of this backup, and whether each holds now. |
| `POST /api/v1/admin/backups/{name}/restore` | Restore. Body: `confirm`, optional `passphrase`. Answers 422 when the backup needs a passphrase other than the stored one. |
| `POST /api/v1/admin/backups/{name}/upload-remote` | Retry the remote copy. |
| `PUT /api/v1/admin/backups/settings` | Change the schedule and remote settings; set the passphrase. Only the parts sent are changed. Secrets are write-only; an empty remote secret keeps the stored one. |
| `POST /api/v1/admin/backups/settings/test-remote` | Test the remote storage settings. |

- `{name}` must be a file name the server would generate: it is matched against a strict
  pattern and never used as a path.
- Create, upload, download, delete, restore and settings changes are written to the audit
  trail with the acting user. The passphrase and keys are never logged.
- Backup and restore are not exposed through the MCP tools.

## Admin page

A new page, **Admin → Backups**, visible to administrators only.

- **Status line:** whether a passphrase is set, the schedule in words, the last scheduled
  run and its result, and whether the remote copy is on.
- **Backups table:** time, kind, size, schema compatibility, remote copy status, and the
  actions Download, Restore, Retry upload (when relevant), Delete. A reminder under the
  table says that backups on the server's own disk do not survive losing that disk.
- **Create backup** and **Upload backup** buttons.
- **Restore dialog:** what will be replaced, what is kept, the checklist of preconditions
  with their current state (maintenance mode on, no running builds, compatible schema), a
  passphrase field shown only when the stored one does not open the file, and a field to
  type `restore`.
- **Settings:** passphrase (set or change), schedule, retention, and remote storage with
  Test connection.

## Error handling

| Situation | Result |
|---|---|
| No passphrase set | Creating a backup is refused with a message that says where to set it; the scheduled task records the same reason. |
| Wrong passphrase, or a file that was altered or cut short | The restore is refused before anything changes: "The passphrase is wrong or the file is damaged." |
| Not a backup file, unknown format version, other schema revision | Refused on upload or restore with the specific reason. |
| A precondition for restore is not met | Refused, naming the precondition. |
| The pre-restore backup cannot be written | The restore does not start. |
| An error while applying the backup | The transaction is rolled back; the server is unchanged; the error is reported and logged. |
| The search index cannot be rebuilt after a restore | The restore still succeeds; the page says the index should be rebuilt. |
| The remote upload fails | The backup succeeds; the remote status shows the failure and can be retried. |
| The backups directory is not writable or the disk is full | The backup fails with the system's reason; nothing partial is left listed. |
| A second backup or restore is started while one runs | Refused: one at a time. |

## Security

- Admin-only, enforced on the server for every endpoint.
- A backup file is useless without its passphrase. The stored passphrase and the remote
  secret key are encrypted with the server key and write-only through the API.
- The key is derived with scrypt, so guessing a passphrase offline is slow; the 12-character
  minimum makes it impractical.
- File names from the client are validated against the generated-name pattern; uploads are
  size-limited and validated before being stored.
- A restore is the most powerful action in the system: it can replace every account and
  secret. It needs an administrator, maintenance mode, a typed confirmation, and it leaves a
  pre-restore backup and an audit entry behind.

## Testing

**The file (pytest)**
- Round trip of the format; the header is readable without the passphrase.
- A wrong passphrase, a changed byte in the header or the body, and a truncated file are
  each rejected.

**Export and restore (pytest, in-memory database)**
- Export a seeded configuration, restore it into an empty database and into a changed one;
  the configuration tables then equal the export.
- Secrets, channel configuration, git credentials and webhook secrets exported under one
  server key are readable after a restore under another.
- Rows created after the backup are removed; builds of surviving pipelines are kept with
  their logs; builds of removed pipelines are gone; a build triggered by a removed user
  stays without a user.
- An error midway leaves every table unchanged.
- The restoring administrator keeps their password and admin status: when the backup lacks
  the account, when it has it by id, and when it has it by email.
- Backup settings are neither exported nor changed by a restore.
- Agents are offline after a restore; pending builds are cancelled.

**Preconditions and API (pytest)**
- Each precondition refuses the restore with its own message; the pre-restore backup exists
  after a successful restore.
- Every endpoint returns 403 for a non-admin and for a project-scoped role.
- File names that are not generated names are rejected; an oversized or invalid upload is
  rejected.
- One backup or restore at a time.

**Schedule (pytest)**
- The due-time rule for daily and weekly, including a missed slot and the first run after
  enabling.
- Retention deletes only scheduled backups, oldest first.
- A failed scheduled run is recorded and notifies administrators.

**Remote copy (pytest, fake S3 server)**
- Upload after a backup, the per-file status, retry, delete, retention, and Test connection
  succeeding and failing.
- A failing remote never fails the backup.

**Frontend**
- `npx tsc --noEmit`, and a manual pass through the page: create, download, upload, restore
  with each precondition unmet and then met, settings, and the remote test.

## Files touched

- `backend/app/services/backup/` — **new** package: the file format, the export, the
  restore, local storage of backup files, the schedule rule, the remote copy.
- `backend/app/api/v1/backups.py` — **new**: the admin endpoints; registered in the router.
- `backend/app/tasks/` — the periodic task and its beat entry.
- `backend/pyproject.toml` — an S3 client library.
- `frontend/src/app/admin/backups/page.tsx` — **new** page; a link in the admin
  navigation; API client functions in `frontend/src/lib/api.ts`.
- `README.md` — a section on backup, restore, the passphrase and the remote copy.
- `backend/tests/` — new tests.

No database migration: settings use the existing system settings table, and backups are
files.

## Rollback

Reverting the code removes the page, the endpoints and the task. Backup files stay in the
backups directory and remain valid for a later version with the same format. The backup
settings rows in the system settings table are ignored by older code.
