# ScaleNexusAI Code Completion Sprint Plan

## Scope Boundaries
- Includes only codebase completion tasks (backend, frontend, tests, data model, integrations).
- Excludes documentation deliverables (HIPAA/PHIPA/PIPEDA documents).
- Excludes deployment/infrastructure rollout tasks.

## Role Split
- `Senior Track`: architecture, schema/migrations, security-critical flows, complex integrations, final code reviews.
- `Junior Track`: UI implementation, endpoint wiring, CRUD flows, test authoring, regression fixes under senior guidance.

## Current Baseline (from repository review)
- Supabase invite + token exchange exists.
- Retell webhook idempotency exists.
- Core call logs/dashboard exist (single-institution style).
- Major gaps remain in 3-tier RBAC, org/clinic scoping, aggregate analytics, post-call pipeline completeness, required screens, and test coverage.

---

## Sprint 1: RBAC Foundations + Data Model (7 days)

### Objectives
- Replace current role model with `org_admin`, `clinic_admin`, `staff`.
- Implement Organization -> Clinics -> Users scoping across data model and APIs.
- Prepare migration-safe transition from `institution/location` naming to unified behavior.

### Senior Track
- Design and implement schema changes:
  - `organizations` table (or equivalent extension of institution model).
  - Add `org_id` to clinic/location records.
  - Update users schema for `org_id`, `clinic_id`, `role`, `status`, `deleted_at`, `last_login_at`, `created_by`.
  - Add org/clinic scoping columns/indexes to PHI-critical tables where missing.
- Write Alembic migrations + backfill strategy.
- Build RBAC policy matrix and shared authorization utility (`authz` layer).
- Implement strict API scope checks (`org_id` + `clinic_id` resource validation).

### Junior Track
- Refactor enum/types on FE and BE to new role names.
- Update API request/response types affected by new role and scope fields.
- Update admin/invite forms to capture role + clinic selection.
- Add unit tests for role parsing, role guards, and basic policy helper coverage.

### Deliverables
- Migrations merged and runnable.
- New role model active in backend and frontend types.
- Shared authorization helper used by core protected endpoints.

### Exit Criteria
- Any user can only access resources within allowed org/clinic scope.
- Legacy role names removed from active auth flow.

---

## Sprint 2: Auth Hardening + User Lifecycle + Audit Compliance Gaps (7 days)

### Objectives
- Enforce required auth/session behavior and close user/audit compliance code gaps.
- Fix reinvite lifecycle with soft-delete and immutable lineage.

### Senior Track
- Implement mandatory MFA enforcement checks in auth/token exchange flow.
- Move local JWT session handling to HttpOnly secure cookie flow (server-set cookie, strict flags).
- Implement reinvite workflow:
  - Soft-delete old user row (no hard delete).
  - Create new user row with new UUID linkage.
  - Preserve PHI references.
- Upgrade audit event model:
  - Store actor `user_id` (UUID), actor role, org/clinic, action/resource/outcome.
  - Add reinvite audit event payload (`old_uuid`, `new_uuid`, actor, timestamp).
- Enforce append-only semantics at data + service layer.

### Junior Track
- Build FE session-expiry warning modal and lockout UX pages.
- Add account-locked and 403/expired-session handling screens.
- Update auth context/client to cookie-based session assumptions.
- Add tests for lockout, reinvite, and audit event emission paths.

### Deliverables
- Reinvite no longer hard-deletes users.
- Audit log events include person-level actor UUID and scoped context.
- Auth/session UX updated for locked/expired states.

### Exit Criteria
- Reinvite flow is traceable end-to-end via immutable audit trail.
- MFA-required users cannot bypass secure login path.

---

## Sprint 3: Voice Functions + Post-Call Pipeline Completion (7 days)

### Objectives
- Complete required Retell-function behaviors and post-call processing reliability.
- Remove high-risk booking and notification gaps.

### Senior Track
- Implement missing Retell function handlers:
  - `transfer_call`
  - `check_insurance`
  - `user_details`
  - explicit backend tool path for `send_sms` in call flow contract
- Fix reschedule transaction logic to `book new first`, then cancel old.
- Add idempotency keys for booking/cancel/reschedule function calls.
- Implement S3 recording persistence + secure signed URL retrieval for call detail playback.
- Add robust retry/backoff wrappers for external call points and graceful fallback tagging (`needs_callback`).

### Junior Track
- Implement call-detail UI updates for signed audio playback integration.
- Add SMS history display per call (content metadata + delivery status fields).
- Add backend endpoint wiring for SMS history retrieval.
- Add integration tests for:
  - duplicate webhook replay
  - duplicate function-call replay
  - reschedule safety (original appointment preserved on book failure)

### Deliverables
- Required Retell function set completed in backend.
- Rescheduling behavior corrected and tested.
- Audio playback moves to signed URL path.

### Exit Criteria
- No double-booking from retries.
- Post-call records remain idempotent under replay.

---

## Sprint 4: Org Admin Aggregate + Clinic Views + User Management (10 days)

### Objectives
- Ship multi-clinic product surface and role-specific visibility.
- Build organization-level analytics and drill-down workflows.

### Senior Track
- Implement aggregate APIs for Org Admin:
  - summary cards (calls, bookings, new patients, minutes, callbacks)
  - clinic comparison table metrics
  - ROI/revenue calculations using configurable avg revenue fields
  - date-range filter engine and clinic drill-down APIs
- Implement org-level user management backend:
  - invite/deactivate/reinvite by role rules
  - org-wide listing and scoped filtering
- Add query optimization/index tuning for aggregate endpoints.

### Junior Track
- Build Org Admin frontend screens:
  - Organization Overview
  - Clinic Comparison
  - ROI panel
  - Drill-down + back-to-overview
  - Organization user management page
  - Add clinic screen
- Build Clinic Admin / Staff role-specific layout restrictions:
  - hide analytics/settings/user mgmt for staff
  - keep operational tools for staff
- Implement missing empty/error states on all new screens.

### Deliverables
- Org Admin multi-clinic analytics and drill-down experience.
- Clinic Admin and Staff views correctly segmented.

### Exit Criteria
- Org Admin can compare clinics and drill into one clinic seamlessly.
- Staff cannot access restricted analytics/settings/user-management routes.

---

## Sprint 5: Realtime Notifications + UX Completeness + Screen Parity (7 days)

### Objectives
- Replace polling-only experience with real-time notifications/events.
- Close required screen and interaction gaps.

### Senior Track
- Implement server real-time event layer (WebSocket or SSE):
  - new call
  - new callback
  - callback resolved
  - appointment booked
  - urgent call alerts
- Implement connection lifecycle:
  - reconnect
  - connection-state indicator
  - graceful fallback to polling
- Add backend event publisher hooks in post-call and callback state changes.

### Junior Track
- Build notification bell, unread list, mark-as-read flow.
- Add badge counters in sidebar/navigation.
- Implement toast behavior for standard vs urgent events.
- Build missing auth/support screens:
  - MFA challenge screen wrapper flow (if custom step needed)
  - account locked screen
  - session expiry modal handler
- Complete responsive behavior on target desktop/tablet breakpoints.

### Deliverables
- Real-time dashboard updates with notification UI.
- Full required screen parity for auth and notifications.

### Exit Criteria
- New calls and callback state changes appear without manual refresh.
- Notification center + badges + toasts function across roles.

---

## Sprint 6: Test Matrix Closure + Stabilization (7 days)

### Objectives
- Achieve stable CI and full boundary coverage for critical flows.
- Remove stale tests and lock behavior with regression suites.

### Senior Track
- Define final RBAC boundary matrix (`3 roles x key endpoints x cross-clinic/org attempts`).
- Author/oversee high-risk integration tests:
  - auth exchange + MFA enforcement path
  - user reinvite + soft-delete invariants
  - webhook + function idempotency
  - aggregate analytics correctness across clinics
- Fix systemic test debt (old tenant-era test suite, broken imports, obsolete fixtures).

### Junior Track
- Implement endpoint-level unit/integration tests from senior matrix.
- Add FE route-guard tests and role-visibility tests.
- Add API contract tests for dashboard filters/search/sort/pagination.
- Execute regression cycles, capture defects, and land fixes.

### Deliverables
- Green CI test run with updated suite.
- Coverage for RBAC boundaries and idempotency behavior.

### Exit Criteria
- No stale tenant-era failing tests remain.
- Critical acceptance flows are covered by automated tests.

---

## Dependency Map (Critical)
- Sprint 1 is prerequisite for Sprints 2, 4, and 6.
- Sprint 2 (auth/audit lifecycle) should complete before finalizing Sprint 6 test closure.
- Sprint 3 post-call changes should precede Sprint 5 realtime event UX wiring.
- Sprint 4 APIs should land before Sprint 4 FE completion and Sprint 6 analytics tests.

## Cross-Sprint Non-Negotiables
- No new endpoint without role+scope authorization checks.
- No PHI-bearing action without audit event emission.
- No external integration call without retry/failure-handling path.
- All feature PRs require tests for success + permission-denied + cross-scope denial.

## Suggested Team Cadence
- Senior engineers own architecture PRs first in each sprint (days 1-3).
- Junior engineers parallelize UI/API wiring once contracts are merged (days 3-6).
- Final sprint day reserved for regression + hardening + merge cleanup.

## Final Outcome Target
- Complete codebase alignment to unified scope behavior for:
  - 3-tier RBAC and multi-clinic access boundaries
  - auth/session/lifecycle/audit correctness
  - voice + post-call reliability and idempotency
  - org/clinic/staff dashboards and notifications
  - automated boundary/regression test confidence
