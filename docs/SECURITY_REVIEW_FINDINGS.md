# Security Review Findings — HIPAA Readiness

**Branch:** `feature/supabase-to-aws-migration`
**Date:** 2026-04-23
**Scope:** Backend auth, PHI handling, infra deployment

Every finding below has been verified against the current code on this branch. File paths and line numbers reflect the state at review time.

---

## Verdict (short version)

Fixing the P0s and P1s below brings the system to **HIPAA-*capable*** — technically defensible for a production deployment when paired with the organizational controls listed at the bottom. It does **not** by itself make you "HIPAA compliant"; compliance is an operational posture (BAAs signed, policies documented, workforce trained, incident-response runbooks rehearsed, risk analysis on file), not a code state.

**After P0 + P1 are fixed, the code is ready. HIPAA compliance still requires the organizational checklist at the end of this document.**

---

## P0 — Must fix before production launch

### P0-1. Open redirect in password-reset / invite emails

**Location:** `src/app/services/auth_email_service.py:104-106`, called from `src/app/api/routes/auth.py:49-51, 288`.

**Behavior:** `ForgotPasswordRequest.redirect_url` is user-controlled. `_resolve_redirect_url` accepts *any* `http://` or `https://` URL verbatim and appends the password-reset token as a query parameter. An attacker who knows a victim's email can force the reset email to point at an attacker-controlled host, receiving the reset token when the victim clicks.

**Why P0:** Pre-auth, remote, full account takeover. Affects every local-auth user, including super-admins.

**Fix:**
- Allowlist `redirect_url.netloc` against `settings.auth_frontend_base_url` host (and any explicitly-configured additional hosts).
- Reject `http://` in production; require `https://` unless `app_env != "production"`.
- Reject URLs where `netloc` is empty, contains credentials (`user:pass@`), or contains a newline / CRLF.
- Same treatment for invite-flow `redirect_url`.

---

### P0-2. Encryption key secret generated with wrong format

**Location:** `infra/nex_health_infra/stack.py:76-84` vs. `src/app/models/institution.py:37-48`.

**Behavior:** The application requires `ENCRYPTION_KEY` to be base64 that decodes to **exactly 32 bytes**. CDK generates a 64-char alphanumeric password via `SecretStringGenerator(exclude_punctuation=True, password_length=64)`. Decoded as base64, that yields 48 bytes, not 32 — the app raises `RuntimeError` on first encryption attempt.

**Why P0:** Either you have never booted against the CDK-generated secret (in which case someone is quietly overriding it by hand), or encryption simply doesn't work. Either way, the code path that's supposed to protect PHI-at-rest is untested end-to-end against the declared infra.

**Fix (pick one):**
- **Preferred:** Replace the CDK `Secret` with a CloudFormation custom resource (Lambda) that generates `base64.urlsafe_b64encode(secrets.token_bytes(32))` at stack deploy time and stores it in Secrets Manager.
- Or: accept a 32-byte raw string and change the app to hex-decode / base64-decode with more tolerant parsing — but infra-side generation of a proper base64-32-byte value is cleaner.

---

### P0-3. HTTP fallback in ALB when cert/domain not configured

**Location:** `infra/nex_health_infra/stack.py:487-492`.

**Behavior:** When `service_config.domain_name`, `certificate_arn`, or `hosted_zone_name` is missing, the stack silently falls through to `listener_port=80` — plain HTTP. `docs/DEPLOYMENT_AND_HIPAA_GUIDE.md:79` acknowledges this as "ACTION REQUIRED FOR PROD."

**Why P0:** HIPAA §164.312(e)(1) — *Transmission security*. ePHI over plaintext HTTP is a breach-level control failure. "We document it" is not a control.

**Fix:**
- If `config.environment_name == "production"` (or an explicit `require_tls=True` flag), raise in `__init__` when certs/domain are missing. Don't synthesize an HTTP-only stack in prod.
- For staging, log a loud warning, and prefer issuing an ACM cert automatically via `DnsValidatedCertificate` when a hosted zone is configured.

---

## P1 — Fix before launch or within the first sprint after

### P1-1. JWT missing `iss` / `aud` / `jti` validation

**Location:** `src/app/api/deps.py:28-33`.

**Behavior:** `jwt.decode` is called with only `algorithms=[settings.jwt_algorithm]`. No `audience=` / `issuer=` kwargs, no `jti` deny-list check.

**Why P1:** A JWT signed by this service's secret is accepted by any endpoint, for any purpose, for its full TTL. If the secret is ever reused across services (common in early-stage infra), cross-service token confusion is possible. Also blocks per-token revocation.

**Fix:**
- Set `jwt_issuer` and `jwt_audience` in config; pass both to `jwt.encode` / `jwt.decode`.
- Add a `jti` claim on issue; when logout or password-reset happens, add the `jti` to a short-TTL Redis deny-list (TTL = access-token remaining lifetime).

---

### P1-2. Logout doesn't revoke active access token

**Location:** `src/app/services/refresh_token_service.py:107` (revokes refresh only).

**Behavior:** Logout kills the refresh token. The access JWT is stateless and stays valid until expiry (default 15 min per `src/app/config.py:109`).

**Why P1:** Combined with P1-1, a stolen access token survives logout for up to 15 minutes with no server-side kill switch.

**Fix:** Solved by P1-1's `jti` deny-list — on logout, push the access token's `jti` into Redis with TTL = `exp - now`. `get_current_user` rejects denied `jti`s.

---

### P1-3. Unsalted SHA-256 for phone-number lookup hash

**Location:** `src/app/models/contact.py:130-134`.

**Behavior:** `hashlib.sha256(normalized_phone)` is deterministic and unkeyed. Phone-number keyspace (~10¹⁰ for North America) is exhaustively searchable in minutes on any GPU. A DB snapshot leak reveals every caller's phone number.

**Why P1:** Phone numbers are PHI when associated with a healthcare interaction. Under HIPAA Safe Harbor de-identification, a value is considered identifiable if it can be re-identified through "readily available" means — an unsalted hash of a 10-digit number qualifies.

**Fix:**
- Replace with HMAC-SHA256 keyed with a per-deployment "phone pepper" stored in Secrets Manager (or derived from `ENCRYPTION_KEY` via HKDF with a fixed info string, e.g. `b"phone-lookup-hmac-v1"`).
- Backfill existing rows via a migration task that re-hashes on next phone decrypt/write.

---

### P1-4. Unsalted SHA-256 for log-identifier hashing

**Location:** `src/app/retell/security.py:146-159`.

**Behavior:** `hash_for_logging` returns `hashlib.sha256(value)[:16]`. Truncation to 16 hex chars actually helps a bit (collisions), but it's still unsalted.

**Why P1:** Logs often escape to third-party systems (CloudWatch, aggregators, SIEM). Same HMAC-with-pepper fix as P1-3.

**Fix:** HMAC-SHA256 with a dedicated `LOG_HASH_KEY` pepper, truncated to 16 hex chars.

---

### P1-5. `X-Forwarded-For` trusted without proxy allowlist

**Location:** `src/app/api/routes/auth.py:82-86`.

**Behavior:** `_client_ip` takes `X-Forwarded-For`'s first hop unconditionally. If a caller reaches the app bypassing the ALB (e.g., a misconfigured security group, an internal attacker), they control the audit IP.

**Why P1:** Audit-log integrity. Lower-severity because it doesn't grant access, but it erodes forensic value.

**Fix:** Only honor XFF when `request.client.host` is in a configured list of trusted proxy CIDRs (ALB subnet CIDRs). Otherwise use `request.client.host` directly.

---

## P2 — Known gaps, defensible as "accepted risk" with compensating controls

### P2-1. PHI fields stored plaintext (contact names, call transcripts, call summaries)

**Locations:**
- `src/app/models/contact.py:61-63` — `first_name`, `last_name`, `full_name`
- `src/app/models/call.py:126, 130-131, 133` — `transcript`, `transcript_with_tool_calls`, `scrubbed_transcript_with_tool_calls`, `summary`

**Status:** Currently protected by RDS-at-rest encryption, RBAC, institution/location scoping, and audit logging. HIPAA does not *require* application-layer encryption on top of that, but:

- Under HIPAA, PHI-at-rest encryption is *addressable* (not required) — §164.312(a)(2)(iv). You must either implement it or document a reasonable equivalent with a risk analysis.
- A snapshot backup that ends up in the wrong S3 bucket exposes this data in cleartext.

**Recommended direction (not blocking):**
- Encrypt `call.transcript` and `call.summary` at the application layer, same pattern as the other encrypted fields.
- Leave contact names plaintext if the dashboard usability case outweighs the risk — but document this as an accepted risk in your risk register, signed off by a privacy officer.

---

### P2-2. No MFA

**Behavior:** Local auth only, no TOTP / WebAuthn / SSO.

**Status:** MFA is not strictly required under HIPAA but is expected in modern risk analyses for any user with PHI access. Acceptable to defer if:
- You target SSO via an IdP (Okta / Entra ID / Google Workspace) within 1–2 quarters, OR
- You add TOTP for INSTITUTION_ADMIN and SUPER_ADMIN roles before scaling past the first clinic.

---

### P2-3. HS256 symmetric JWT

**Status:** Fine for a single-service monolith. If you split into microservices sharing auth, move to RS256 with key rotation.

---

## Fix-order recommendation

1. **P0-1** (open redirect) — smallest diff, highest-impact fix, probably one afternoon.
2. **P0-2** (encryption key format) — one CDK change, redeploy staging, verify encryption round-trip end-to-end.
3. **P0-3** (HTTP fallback) — make it a hard failure in production config.
4. **P1-1 + P1-2** together — `iss`/`aud`/`jti` validation and access-token deny-list. One coherent change.
5. **P1-3 + P1-4** — introduce HMAC peppers + migration for phone hashes.
6. **P1-5** — trusted-proxy XFF handling.
7. P2 items as the roadmap allows.

---

## After P0+P1 are fixed — is HIPAA ready?

**Technical controls: yes, defensible.** The code will have:

- Salted bcrypt passwords, lockout, refresh rotation, access-token revocation, `iss`/`aud` validated JWTs
- AES-256-GCM for secrets and PHI-tagged fields, HMAC-keyed lookup hashes
- TLS-only in production (ALB → ECS), encrypted RDS + Redis, WAF at edge
- Immutable audit logs with DB-trigger enforcement
- Role + tenant + location scoping on all PHI-bearing routes
- Webhook signature verification on all external ingress
- Security headers, no-store cache, CORS allowlist

**Organizational controls still required for actual HIPAA compliance** (no amount of code fixes substitutes for these):

- [ ] **Business Associate Agreements (BAAs)** signed with: AWS, Retell, Resend (email provider), Twilio, NexHealth, Sentry / any observability vendor, any LLM provider. *No BAA = no PHI allowed through that vendor.*
- [ ] Designated Security Officer and Privacy Officer (§164.308(a)(2))
- [ ] Written HIPAA policies and procedures (§164.316)
- [ ] Workforce training records (§164.308(a)(5))
- [ ] Risk analysis and risk management plan on file (§164.308(a)(1)(ii)(A-B))
- [ ] Sanction policy for workforce violations (§164.308(a)(1)(ii)(C))
- [ ] Contingency plan: data backup, disaster recovery, emergency-mode operation (§164.308(a)(7))
- [ ] Breach notification procedure documented and rehearsed (§164.400-414)
- [ ] Access authorization + termination procedures (§164.308(a)(3-4))
- [ ] Periodic audit-log review cadence (you have the logs — who reads them weekly?)
- [ ] Data retention and disposal policy (how long do you keep transcripts? how are backups destroyed?)
- [ ] Facility access controls for any on-prem admin (§164.310) — N/A if fully cloud
- [ ] Annual or event-triggered penetration test — external, documented

**Bottom line:** Land P0 + P1 and the engineering side clears the bar. Compliance ship-readiness additionally depends on the organizational checklist above. If the BAAs aren't signed (especially with Retell, Resend, Twilio, and your LLM provider), the system is *technically* HIPAA-capable but *legally* not compliant for real PHI.
