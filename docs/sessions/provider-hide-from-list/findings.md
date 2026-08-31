# Findings

## Existing plumbing (reused, not rebuilt)

`institution_providers` is already a per-location cache of PMS providers carrying
operator-owned settings, so this feature is one more field on an existing rail:

- `src/app/models/institution_provider.py` — `buffer_minutes`, `same_day_cutoff_time`,
  `min_age`, `max_age` are all operator-set on this table.
- `src/app/api/routes/institution_setup.py:441` — `PATCH /providers/{id}` with a
  `model_fields_set` tri-state pattern (set / explicit-null / untouched).
- `nexus-dashboard-web/src/pages/ProvidersScheduling.tsx:130-191` — provider settings
  panel with dirty-check + save.
- `tests/integration/test_provider_age_group.py` — the closest precedent test.

## Why is_hidden is a new column and not `is_active`

`SyncService._upsert_provider` (`src/app/services/sync_service.py:223`) sets
`existing.is_active = True` on **every** sync run. `is_active` means "seen in the last
PMS sync", not operator intent. Hiding via `is_active` would be silently undone by the
next sync.

Worse, in the Retell tool it currently backfires: `handlers.py:1170` builds the age-rule
map from `is_active=True` rows only, and any provider with no map entry hits
`if rule is None: filtered.append(p)` — include-by-default. So `is_active=False` makes a
provider *more* likely to be listed.

## Retell tool structure

`src/app/retell/handlers.py:1122 list_providers` calls `ctx.adapter.list_providers()`
(a live NexHealth fetch), then applies age filtering **only** inside
`if patient_age is not None and ctx.location:`. With no DOB supplied there is no local
filtering at all. The hidden filter therefore must sit outside that block.

`ctx.location` is guaranteed non-None — `_resolve_context` fails closed rather than
routing to a default clinic — so a location-scoped filter is always applicable.

### ID format
`mappers._pid` prefixes with `nh-`, so `UniversalProvider.id == "nh-123"`, and
`sync_service` stores `source_id = p.id` — already prefixed. The existing age lookup
`age_rules.get(f"nh-{p.id}") or age_rules.get(str(p.id))` therefore probes `nh-nh-123`
first and only matches via the fallback. Left as-is (tested behaviour); the new hidden
filter matches on `p.id` directly.

## Alembic: prod and staging lines have diverged

| Line | Branch | Revisions | Head |
|---|---|---|---|
| prod | `hotfix/lookup-identity-gate` | 21 | `20260720_call_scrubbed` |
| staging | `deploy/multi-agent` | 54 | `20260801_gotracker_location_webhook_secret` |

They fork after `20260720_call_scrubbed`; staging carries 33 revisions prod has never seen.

This migration chains off the **prod** head, which is correct for a prod-targeted change
(chaining off the staging head would make prod try to apply all 33 intervening
revisions). Consequence: merging the two lines yields **two heads** and
`alembic upgrade head` fails with "Multiple head revisions are present". Resolve with a
merge revision — the repo already does this in
`20260721_merge_call_scrubbed_nexhealth_durability` (tuple `down_revision`).

Failure mode is safe: prod runs migrations between image-push and traffic-shift, so a
multiple-heads error gates the rollout instead of half-applying.

### Trap: revision-id length differs per environment
`20260713_campaign_overview_indexes.py` widens `alembic_version.version_num` to
`varchar(64)` — and that migration exists **only on the staging line**. Prod is still at
alembic's default `VARCHAR(32)`.

So any revision id over 32 chars works on staging and **fails on prod**. The existing
merge revision name (`20260721_merge_call_scrubbed_nexhealth_durability`, 49 chars) would
not run on prod. Whoever writes the future merge revision must keep the id <= 32 chars.
This migration's id is 24.

### No schema conflict
Of all 54 staging revisions, only the shared `20260510_consolidated_baseline.py` touches
`institution_providers`. The new column is additive, `IF NOT EXISTS`, with a server
default, so it is order-independent with respect to the staging line.
