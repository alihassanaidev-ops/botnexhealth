# Security Model

Last reviewed: June 2026. This covers the application layer: identity, tenant
isolation, PHI handling, retention, and audit. Infrastructure-side controls
(network isolation, encryption in transit/at rest on AWS, IAM) are in
[DEPLOYMENT_AND_HIPAA_GUIDE.md](DEPLOYMENT_AND_HIPAA_GUIDE.md).
"HIPAA-minded" throughout means we implement the technical safeguards; it is
not a compliance certification claim.

## Identity and sessions

- **Access tokens**: JWT (HS256, issuer + audience enforced), 15-minute TTL,
  unique `jti` per issuance. JTI revocation is checked in Redis on every
  request, so logout/admin-revoke is immediate, not eventual
  (`src/app/services/auth.py`, `src/app/api/deps.py`).
- **Refresh tokens**: opaque 32-byte secrets in an HttpOnly Secure cookie
  scoped to `/api/auth`, stored hashed in Redis. Rotation on every refresh with
  **replay detection** — a rotated token's hash is remembered for 24h, and
  presenting it again revokes the whole session family
  (`src/app/services/refresh_token_service.py`). Idle window keeps HIPAA
  auto-logoff: refresh extends the session, inactivity ends it; the frontend
  mirrors this with a 15-minute inactivity logout.
- **Passwords**: Argon2id (t=3, m=64MiB, p=4), 12-char minimum. Login performs
  a dummy Argon2 verify when the user doesn't exist, so response timing doesn't
  leak account existence (`src/app/api/routes/auth.py:841-844`). Lockout after
  5 failed attempts for 30 minutes.
- **Invites / password resets**: single-use tokens stored hashed, TTL-bound,
  with resend cooldown (exponential backoff) to prevent invite spam.

## MFA

Required for all dashboard roles. Methods (`src/app/services/mfa.py`,
models in `src/app/models/mfa.py`):

- **WebAuthn/passkeys** — preferred. `WEBAUTHN_RP_ID` and HTTPS-only
  `WEBAUTHN_ALLOWED_ORIGINS` are required in production; credential
  `sign_count` is tracked for clone detection.
- **TOTP** — secret stored encrypted, standard 6-digit codes.
- **Recovery codes** — hashed, single-use, shown once at enrollment.

Login is two-phase: password success returns an MFA ticket (10-minute Redis
entry) rather than tokens; the ticket is bound to the client's /24 (IPv4) or
/64 (IPv6) so it can't be replayed from elsewhere. The same machinery powers
step-up prompts for sensitive in-session actions. MFA events are audit-logged
with hashed identifiers only.

## Authorization

Five roles (`src/app/models/user.py`): `SUPER_ADMIN` (platform operator,
cross-tenant), `INSTITUTION_ADMIN` (one clinic, all locations), `LOCATION_ADMIN`
and `STAFF` (one location), and `GROUP_ADMIN` (read-only oversight across an
`InstitutionGroup`, confined to `/group/*` and walled off from all PHI/setup/
write/call routes). Enforcement is split across two files: role gates in
`src/app/api/deps.py` (`get_current_*` dependencies that 403 on role mismatch)
and location-scope pins in `src/app/api/deps_scope.py` — location-scoped users
are pinned to their assigned location and any explicit `location_id` mismatching
it is a 403. Location users additionally see only contacts granted to their
location via `contact_location_accesses` (404, not 403, when no grant exists).

Full role inventory, the dependency-function list, the `ContactLocationAccess`
visibility model, and a role→route matrix are in
[REPOSITORY_CONTEXT.md](REPOSITORY_CONTEXT.md#3-rbac--permission-model).

The voice agent is its own principal: Retell requests authenticate by HMAC
signature, are scoped by the agent→location mapping, and `lookup_patient`
additionally requires the *caller* to pass an identity gate (DOB + exact email
or phone-last-4) before PHI is spoken back.

## Tenant isolation

Two independent layers — application-level scoping and Postgres row-level
security — described in [ARCHITECTURE.md](ARCHITECTURE.md#multi-tenancy).
The short version: every tenant-scoped table has an RLS policy keyed on
`current_setting('app.institution_id')`, the runtime role cannot bypass RLS,
startup logs CRITICAL for any tenant table missing a policy, and an `rls`
pytest tier exercises the policies against real Postgres. An application-layer
bug that drops a `WHERE institution_id` filter returns zero rows instead of
another clinic's data.

## PHI at rest

Application-layer encryption on top of RDS volume encryption:

- AES-256-GCM, random 96-bit IV per value, key derived via HKDF-SHA256 from
  `ENCRYPTION_KEY` (`src/app/security.py`).
- Encrypted columns: contact email/phone/DOB, call transcript/summary, SMS
  recipient/body, notification title/message/payload, dead-letter payloads,
  TOTP secrets, the institution NexHealth key.
- Lookups over encrypted fields use separate keyed hashes (e.g. `phone_hash`
  for caller-ID matching) so we never need to decrypt-and-scan.
- Config enforces `ENCRYPTION_KEY` present in production and distinct from
  `JWT_SECRET` (`src/app/config.py:203-211`) — rotating JWTs must never be an
  accidental PHI-key rotation.

Call recordings: only Retell's *scrubbed* recording/transcript artifacts are
ever stored (raw ones are never fetched); audio goes to a private S3 bucket
under `recordings/{institution_id}/{call_id}` with retention tagging.

Logging: structured JSON, request-scoped IDs, and a hard rule of no
bodies/PHI in logs — upstream error bodies are logged as status + byte count
only, SMS/provider errors pass through a phone/email/DOB redactor
(`src/app/services/sms_privacy.py`).

## Retention and deletion

`src/app/services/retention_policy.py`, driven by `RETENTION_*` settings and a
daily scheduled job. Defaults: clinical records (transcripts/summaries)
10 years, recordings 90 days, SMS bodies 10 years with metadata kept 6,
dead-letter raw payloads 30 days, idempotency rows 7 days. The policy-level
schedule and the legal-hold process are written up in
[compliance/policies/retention-destruction-legal-hold.md](compliance/policies/retention-destruction-legal-hold.md).

Purge semantics are deliberately not row deletion:

- Calls are purged in place (encrypted fields nulled, `purged_at` stamped) so
  metrics and audit references survive; `legal_hold_until` blocks purging.
- Contacts are **anonymized**, not deleted, once their last call is purged —
  identity fields cleared, `anonymized_at` set, row retained for FK integrity.
- Recordings are deleted from S3.

## Audit logging

`audit_logs` is append-only at the database layer (trigger rejects
UPDATE/DELETE, plus TRUNCATE protection), range-partitioned by month with
partitions pre-created daily. Writes are batched in-background and explicitly
drained on shutdown so rolling deploys don't drop entries
(`src/app/services/audit.py`). Each entry carries actor, action, resource,
tenant context, and the institution's jurisdiction (drives provincial
breach-notification rules; deployment is `ca-central-1`).

## Communications compliance

Outbound SMS (`src/app/services/sms_compliance.py`) passes three gates in
order: do-not-contact registry → active suppression (STOP keyword via Twilio
webhook, or manual) → latest consent record (append-only grant/revoke log).
Every message gets the clinic identity line and a CASL/TCPA footer
("Reply STOP to opt out…"). Send history is logged with encrypted body and
masked number. Twilio status callbacks are signature-verified.

Email (Resend) uses idempotency keys per send; staff-facing templates mask
patient names (`J***`), only patient-facing confirmations carry the full name.

## Known gaps and accepted risks

Kept honest on purpose — these are the items a reviewer should know about:

- **SMS quiet hours are not implemented.** Sends are consent-gated but can go
  out at any hour. TCPA exposure is limited (messages are transactional,
  responses to the patient's own call) but a quiet-hours window is on the
  backlog.
- **Audit-log retention/archival is not yet automated.** Partitions make
  drop-by-month trivial, but no job enforces the 6-year HIPAA window or
  archives old partitions yet.
- **Per-institution NexHealth credentials are not wired in** — single platform
  key, isolation by subdomain/location (see NEXHEALTH.md). Fail-closed, but a
  platform-key compromise is a platform-wide event.
- **NexHealth token cache and rate limiter fail open on Redis errors** —
  deliberate availability trade, documented in NEXHEALTH.md.
