# HIPAA Code Audit — Remediation Status

**Audit:** `HIPAA_CODE_AUDIT_2026-08-29.md` (staging @ `c4c1783`, 2026-08-29)
**Status as of:** 2026-10-15, branch `fix/platform-completion`
**Task:** Platform completion list, Task 8A · S4 ("fix each item, or document why not").

Findings below are re-checked against the current code. Several were fixed on
`staging` after the audit and are marked **Fixed (pre-existing)**.

## High

| ID | Finding | Status | Notes |
|---|---|---|---|
| H-1 | PHI in URL query strings written to the gunicorn access log | **Fixed** | Access-log half fixed earlier (`access_log_format` logs the path only). The identifiers now travel in the request body on the patient, calls, contacts, callbacks, appointment-sync, enquiries and admin-user searches; non-PHI filters stay in the query. A guard test (`test_no_phi_query_params.py`) fails if any route puts a PHI identifier back into a query parameter. |
| H-2 | NexHealth webhooks authenticated by one platform-wide secret; tenant taken from the payload | **Fixed (pre-existing)** | `_live_signature_candidates` resolves per-subscription secrets from the DB, requires `webhook_subscription_id` + `subdomain` binding fields, and fails closed (`403`) on an unknown subscription (`nexhealth_webhooks.py:514-544`). The global secret is now only a local/test fallback. |

## Medium

| ID | Finding | Status | Notes |
|---|---|---|---|
| M-1 | Whole workflow context sent to OpenAI when `include_context` is on | **Partially fixed** | `_permitted_context` now supports a per-node `context_fields` allowlist (`llm_node_executor.py:190`). An empty allowlist still means "whole context", so the default is unchanged — the minimization only applies where a workflow explicitly sets `context_fields`. |
| M-2 | Entire merge context spread into Retell voice dynamic variables | **Fixed** | `voice_node_executor.py` now builds the dynamic variables from a named allowlist (`_CAMPAIGN_VOICE_VARIABLES`), the voice twin of the SMS list. Free-text clinical content the script does not reference no longer leaves the platform. |
| M-3 | Decrypted AI call summary returned in bulk from the list endpoint | **Open (product decision)** | The list serves the *scrubbed* summary (`calls.py:253`); the raw summary stays on the detail. The residual is that the list audit records only counts. Fix = record returned call ids in the list audit row; deferred pending a calls-screen decision. |
| M-4 | `scrubbed_recording_url` served inline with no reveal/audit | **Fixed** | No longer on `GET /{call_id}`. It is returned by `POST /{call_id}/reveal/recording`, covered by the `VIEW_CALL_RECORDING` reveal and audit. |
| M-5 | No uniqueness binding a NexHealth location/subdomain to one institution | **Fixed (pre-existing)** | `alembic/versions/20260904_nexhealth_mapping_security.py` adds the guard trigger + partial unique index; the model declares the constraint (`institution_location.py:51-55`). |
| M-6 | No replay window on NexHealth webhook signatures | **Fixed** | `_verify_signature` now rejects timestamps older than 300s (`nexhealth_webhooks.py`), mirroring GoTracker. Covered by a new regression test. |
| M-7 | No Content-Security-Policy | **Fixed** | API responses get a locked-down `default-src 'none'` CSP (docs pages excepted); the CloudFront distribution attaches a `ResponseHeadersPolicy` with a same-origin CSP plus HSTS, frame-deny and referrer policy. |
| M-8 | PHI ciphertext has no key-version marker; no rotation path | **Open** | `models/institution.py` encrypts `base64(iv‖ct‖tag)` with no key id and no AAD. Deferred — a keyring + re-encryption migration is a data-migration project, not a code tweak. |

## Low

| ID | Finding | Status | Notes |
|---|---|---|---|
| L-1 | Promised break-glass reveal workflow does not exist | Open | Operational gap; denial is correct and audited. |
| L-2 | Recordings bucket uses SSE-S3, not a CMK | Open | Infra hardening; defer to ops (Task 8B). |
| L-3 | Inbound-email tasks log raw exceptions | **Fixed** | Every inbound-email error path logs `safe_error_summary(exc)` — the exception type and HTTP status only, never the provider message that can echo a patient name. |
| L-4 | Retell signature verification tries three normalizations | Open | Defence-in-depth; tighten once the wire format is confirmed. |
| L-5 | CloudWatch log retention is 1 month | Open | Coupled to H-1; revisit with H-1. |
| L-6 | Bootstrap scripts log staff emails in cleartext | Open | Workforce, not PHI; minor. |

## Summary

- **Fixed:** H-1, H-2, M-2, M-4, M-5, M-6, M-7, L-3. Also fixed a repo-found gap:
  `internal_status_events` had an `institution_id` column but no RLS.
- **Partially fixed:** M-1 (allowlist exists; defaults to whole context).
- **Open, deliberately deferred with a reason:** M-3 (product decision),
  M-8 (data migration), L-1, L-2, L-4, L-5, L-6.

Deferred items are not claimed as done. M-8 needs a ciphertext-format migration;
M-3 needs a calls-screen decision; the L items are hardening or operational.
