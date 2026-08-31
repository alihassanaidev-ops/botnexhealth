# Progress

## Session 1 — implementation

Branch `feat/provider-hide-from-list` off `hotfix/lookup-identity-gate` @ 486651e.
All five phases complete. Nothing applied to any environment: the migration was only
rendered offline with `alembic upgrade … --sql`.

### Changed

| File | Change |
|---|---|
| `alembic/versions/20260819_provider_hidden.py` | new — additive `is_hidden` column |
| `src/app/models/institution_provider.py` | `is_hidden` column + note on why `is_active` is unsuitable |
| `src/app/api/routes/institution_setup.py` | `is_hidden` on response + `UpdateProviderRequest` + handler branch |
| `src/app/retell/handlers.py` | single cache lookup feeding hidden + age filters; hidden dropped unconditionally |
| `nexus-dashboard-web/src/types/index.ts` | `is_hidden` on `CachedProvider` |
| `nexus-dashboard-web/src/lib/tenant-api.ts` | `is_hidden` in the `updateProvider` payload |
| `nexus-dashboard-web/src/pages/ProvidersScheduling.tsx` | visibility checkbox, state, dirty-check, selector marker |
| `tests/integration/test_provider_hidden.py` | new — 7 tests |
| `tests/integration/test_provider_age_group.py` | updated for the changed DB access |
| `tests/unit/test_sync_service.py` | new regression: sync must not clear `is_hidden` |

### Behaviour change worth knowing

`list_providers` previously touched the DB **only** when a `date_of_birth` was supplied.
It now always issues one indexed query on `location_id`, because hiding has to apply
without a DOB. That is one extra round-trip per call on the voice path, and it is why the
two no-DOB cases in `test_provider_age_group.py` needed the session stubbed — they
previously never reached the database.

### Verification

- `alembic heads` → single head `20260819_provider_hidden` (24-char id, within prod's
  `VARCHAR(32)` limit).
- `alembic upgrade 20260720_call_scrubbed:20260819_provider_hidden --sql` → one
  `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.
- `npx tsc --noEmit` → clean.
- `pytest tests/unit tests/integration` → **1156 passed, 38 skipped, 3 failed**.
  The 3 failures (`test_institution_dashboard.py::test_get_dashboard_summary_…`,
  `test_institution_portal.py::test_get_my_institution_context_success`,
  `test_institution_portal.py::test_invite_institution_user_rejects_staff_role`)
  **pre-exist** this work — verified by re-running them with the changes stashed.

### Errors hit and resolved

1. New PATCH tests raised `AttributeError: 'SimpleNamespace' has no attribute 'role'` —
   the route audits `current_user.role`. Fixed by matching the precedent fixture
   (`role="INSTITUTION_ADMIN"`).
2. Nearly shipped an id-matching bug: the age test's `_make_pms_provider` yields **bare**
   ids (`"100"`) while the real mapper yields `"nh-100"`, which is why the age lookup
   probes both forms. The hidden filter now probes both too.

### Not done (deliberately out of scope)

- `pms/nexhealth/adapter.py:400` still fans out over *all* providers when a slot search
  runs without an explicit provider, so a hidden provider can still surface in
  availability and remains bookable by id. Requested scope was the list tool only.
- No book-time enforcement.
- `universal/providers.py` remains a live passthrough with no cache filtering.

### Follow-ups

- Merging the staging line will produce two alembic heads; resolve with `alembic merge`
  and keep the new id <= 32 chars. See findings.md.
- `graphify update .` not run — no graph on this machine (no `graphify-out/`, no CLI).

## Session 2 — production deploy (partial)

Merged to `hotfix/lookup-identity-gate` as `0ccff80` (fast-forward on 486651e).

### Chosen sequence

Pre-apply the column, then roll code, then let the migration task advance
`alembic_version`. `docs/PRODUCTION.md` documents `deploy -> migrate` and flags
migrate-before-traffic gating as **deferred hardening**, so the code would otherwise go
live before the column existed — and the new code `SELECT`s `is_hidden`, which would have
errored `list_providers` and the admin provider list for the length of the gap.

### Done — prod pre-flight (read-only, one-off ECS task on the Migration SG)

```
alembic_version:           20260720_call_scrubbed   (no drift)
is_hidden exists:          False
alembic_version width:     32                       (confirms prod's VARCHAR(32))
institution_providers:     12 rows
```

### Done — prod pre-apply

`ALTER TABLE institution_providers ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE`

```
column_now:                boolean, default false, NOT NULL
rows:                      total=12 hidden=0
alembic_version:           20260720_call_scrubbed   (deliberately unchanged)
```

**This state is stable and can sit indefinitely.** The column exists but no deployed code
references it, so prod behaviour is unchanged and nothing is half-applied.

### Remaining — needs an operator to run

```bash
# 1. Code rollout (builds the image from the working tree)
cd infra && AWS_PROFILE=deployer PATH="$PWD/.venv/bin:$PATH" \
  cdk deploy -c config=config/production.json --require-approval never

# 2. Advance alembic_version. The ALTER above is a no-op (IF NOT EXISTS); this exists so
#    prod's revision matches the code. Must run AFTER step 1 — the migration task runs
#    the *deployed* image, and only the new image contains 20260819_provider_hidden.
AWS_PROFILE=deployer CDK_STACK_NAME=nex-health-production bash scripts/run_ecs_migration_task.sh

# 3. Frontend (the visibility checkbox lives here)
AWS_PROFILE=deployer CDK_STACK_NAME=nex-health-production bash scripts/publish_frontend_from_cdk.sh
```

### Verify after

- `alembic_version` is `20260819_provider_hidden`.
- Providers & Scheduling shows "Voice Agent Visibility"; ticking it and saving persists.
- A hidden provider disappears from the Retell `list_providers` tool.
- `/nex-health/production/api` has no `is_hidden`-related errors.
