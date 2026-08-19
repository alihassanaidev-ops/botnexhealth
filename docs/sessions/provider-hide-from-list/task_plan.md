# Hide providers from the Retell `list_providers` tool

**Goal:** let an operator mark individual providers, per location, as hidden so the
Retell `list_providers` tool stops offering them to callers. NexHealth returns the
clinic's full historical roster; only a few are real bookable staff.

**Scope decided with the requester:** location level only. Effect is limited to the
`list_providers` tool + the admin panel toggle. Explicitly **out of scope** (noted in
findings.md as known gaps): the slot fan-out in `adapter.py`, book-time enforcement,
and the `universal/providers.py` passthrough.

**Base:** `feat/provider-hide-from-list` off `hotfix/lookup-identity-gate` @ 486651e (production).

## Phases

### Phase 1 — Data model
**Status:** complete
- `institution_providers.is_hidden` boolean, NOT NULL, server_default false.
- Alembic migration (revision id <= 32 chars per deploy constraints).
- Sync must never write this column — it is operator-owned, unlike `is_active`.

### Phase 2 — Admin API
**Status:** complete
- `CachedProviderResponse.is_hidden` so the panel can render current state.
- `UpdateProviderRequest.is_hidden` + handler branch, following the existing
  `model_fields_set` tri-state pattern used by min_age/max_age.
- `GET /providers` keeps returning hidden rows so they can be un-hidden.

### Phase 3 — Retell tool filter
**Status:** complete
- `list_providers` drops hidden providers before the age-group pass.
- Filter must run unconditionally, not inside the existing `if patient_age is not None`
  block, which only executes when a DOB is supplied.

### Phase 4 — Admin UI
**Status:** complete
- Checkbox in the provider settings panel of `ProvidersScheduling.tsx`.
- `is_hidden` through `types/index.ts` + `tenant-api.ts`.
- Hidden providers visibly marked in the provider selector list.

### Phase 5 — Tests & verify
**Status:** complete
- Retell tool excludes hidden (with and without DOB).
- PATCH round-trip.
- Regression: a PMS sync must not clear `is_hidden` (the failure mode `is_active` has).
