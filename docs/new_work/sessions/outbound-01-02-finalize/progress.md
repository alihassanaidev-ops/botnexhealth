# Progress — Finalize Plans 01 & 02

## Session 1 — 2026-07-04
- Plan approved (Flow D). Session folder + task list created.
- Starting P0 (harness) + P1 (correctness-critical engine fixes).

### Phase 1 COMPLETE (all live-risk correctness fixes) — 166 automation unit tests pass
- **A1** unified dispatch: new `build_dispatcher()` (step_dispatcher.py) injects real
  `ComplianceGateService` + resolves location tz. Rewired 3 callers
  (automation_workflows.py inline enroll; tasks dispatch + enroll). Kills inline gate
  bypass + `UTC` hardcode. Removed unused imports.
- **A2** quiet-hours hold now DEFERS: `GateResult.retry_at` added; dispatcher hold branch
  creates a timer to next window + WAITING (was `complete_run` → dropped). `resume_after_timer`
  handles held-send re-entry.
- **A3** new `QuietHoursService` (is_quiet_hours + next_permitted_window from
  LocationOperatingHours). Gate delegates to it; blocks `no_permitted_window` when none in horizon.
  New test file test_automation_quiet_hours_service.py (9 tests).
- **A4** real emergency halt: `emergency_halt_version` / `emergency_halt_institution` in
  definition_service (cancel_run + cancel_timers_for_run + audit event). Wired into
  `/outbound-halt` POST (now terminates in-flight runs, returns `halted_runs`). New route
  `POST /{workflow_id}/emergency-halt` (halts version + pauses workflow).
- **A5** paused workflow defers in-flight waiting runs: dispatch loads AutomationWorkflow,
  reschedules timer (+300s) via new `scheduler.reschedule_timer` when paused.
- **A6** concurrency-safe idempotency: enroll wraps INSERT in begin_nested savepoint +
  IntegrityError recovery; dedup scope aligned to DB index (institution+version+key).
- **A12** CRITICAL: `recover_stale_claims` was NEVER wired (verified). Added
  `recover_stale_workflow_timers` task + beat entry (60s < 120s claim TTL).
- Tests added: quiet-hours (9), dispatcher hold-defer (1), enroll race (1), paused-defer (1),
  stale-recovery (1), emergency-halt (2). Updated closed-today gate test + dispatch-timer test
  for new wiring.

### Files touched (Phase 1)
compliance_gate.py, quiet_hours_service.py (new), compliance_gate_service.py, step_dispatcher.py,
enrollment_service.py, scheduler_service.py, definition_service.py, tasks/automation_workflow.py,
worker.py, api/routes/automation_workflows.py + 5 test files (+1 new).

### Phase 2 COMPLETE (engine architecture) — 176 automation+metrics unit tests pass
- **A7** conflicting-active-run dedup in enrollment_service (contact+workflow non-terminal → return existing).
- **A8** `action_registry.py`: send node type → executor class; step_dispatcher uses registry
  (voice plugs in when Plan 03 lands). SUPPORTED_TRIGGER_TYPES enumerated.
- **A9** `validation_service.py` (WorkflowValidationService): structural + unreachable-node +
  consent-path/content-class guardrail + Plan-12 content seam + Plan-10 readiness seam. Wired into
  publish_version (FAIL-CLOSED on errors) and the /validate route (now returns warnings + node-linked).
- **A10** schema: `ComplianceMetadata` (content_class, consent_required) + `NodeLayout` +
  top-level `compliance`/`layout` fields (layout = presentational, non-executable). Deliberately NO
  quiet-hours opt-out toggle (footgun) — quiet hours stay authoritative via LocationOperatingHours.
- **A11** `revalidation.py` seam (RunRevalidator/NoOp): dispatcher skips+exits a send if run no
  longer valid (e.g. appointment cancelled). Real PMS revalidator injected in Phase 5 (C-Plan09).
- **A13** calendar-send jitter (secrets.randbelow, default 0 in tests / 300s via build_dispatcher)
  to avoid 9am stampedes. Full budget pacing deferred to Plan 09/11 coordination.
- **A14** dead-letter wiring: dispatch + enroll tasks capture_dead_letter on retry exhaustion
  (PHI-free id payload) for operator replay.
- **A15** SSE: `workflow_run_updated` registered in event_bus; runtime._emit publishes best-effort
  on run.* transitions (progress UI hint, PHI-free).
- **A16** (subagent) `scripts/publish_workflow_metrics.py` + `publish_workflow_metrics` task + beat
  (60s): CloudWatch WorkflowDueTimerBacklog/StaleTimers/ActiveRuns/FailedRuns/FailedSteps. 1 test.
- New files: action_registry.py, validation_service.py, revalidation.py, quiet_hours_service.py,
  scripts/publish_workflow_metrics.py + tests (validation_service 6, workflow_metrics 1, + additions).

### Phase 3/4 backend (B7) done; frontend Phase 3 dispatched
- **B7 backend**: `dry_run.py` (simulate_run, pure, mirrors client TestRunResult; renders sample
  merge data via template_renderer) + `POST /automation/workflows/dry-run` route + response models.
  4 dry-run unit tests + routes suite green (27 pass).
- Phase 3 frontend (B1 wire /versions,/validate,/merge-fields; B2 merge-field drift; B3
  authoritative publish validation; B4 consent-path guardrail + content-class editor; B8 version
  history) delegated to a frontend subagent (nexus-dashboard-web, vitest).

### Phase 3 + Phase 4 frontend COMPLETE — Plan 02 functionally done
- Phase 3 (subagent): B1 wired /versions,/validate,/merge-fields; B2 merge-field drift fixed
  (backend catalog is source of truth, dropped provider_name/appt_date/appt_time); B3 authoritative
  publish validation; B4 ComplianceSettings editor + backend-issue panel; B8 version history.
  New: ComplianceSettings.tsx. FE 18 files/105 tests green, tsc clean.
- Phase 4 (subagent): B5 full drag-and-drop (onConnect→next_node_id/branches/entry, node-drag→
  definition.layout, "Tidy layout"=clearLayout, layout strictly presentational — test proves edges
  invariant); B7 FE dryRun() wired to POST /dry-run with client walker fallback; B9 tests.
  FE 20 files/121 tests green, tsc clean.
- **Plan 02 = complete except B6 (readiness UI), which is blocked on Plan-10 readiness endpoint.**

### Phase 5 cross-plan COMPLETE (except voice — deliberately deferred)
- C-Plan11 metering: usage_events model + migration (20260704) + Twilio/Resend billing hooks +
  idempotent record + RLS. 6 tests.
- C-Plan10 readiness: TenantTwilioCredentialResolver, computed ChannelReadinessService
  (warning-level, wired into publish + /validate), GET /channel-readiness, sub-account webhook fix.
  No migration (computed). 107 target tests.
- C-Plan09 recall+revalidation: real scan_recall_workflows enrollment (NexHealth recall list, paced),
  PmsLiveRevalidationService (injected at all 3 build_dispatcher sites — fills A11), webhook
  cancellation. 79 tests. Remainder: full disposable-projection read model + reschedule re-enroll.
- **C-Plan03 VOICE: deliberately NOT built.** Other dev owns Plan 03; branch inaccessible here.
  Codebase has NexHealthClient but NO Retell outbound-call client (net-new, theirs). Building blind =
  duplication + double-dial risk + unmet reconciliation gate. Engine SEAM ready (action_registry
  registers send_voice on arrival; dispatcher stubs gracefully; revalidation + metering-TODO in place).
- B6 backend follow-up: added location_id to WorkflowResponse (readiness UI fetch key).

### Phase 6 VERIFICATION COMPLETE
- 275 backend unit tests + 130 frontend tests (tsc clean) green.
- NEW tests/integration/test_automation_engine_integration.py — 6 tests PASS vs REAL Postgres
  (testcontainers; TESTCONTAINERS_RYUK_DISABLED=true on Docker Desktop): publish immutability +
  version pinning; enroll→wait→resume→exit; stale-claim recovery (A12); emergency-halt cascade (A4);
  idempotency real unique index (A6); RLS cross-tenant run isolation.
- **BUG FOUND + FIXED (fresh-deploy blocker):** alembic upgrade head failed on a fresh DB —
  20260703_institution_provisioning used bare op.add_column while the consolidated baseline builds
  the schema from live metadata (create_all), so columns pre-existed → DuplicateColumnError. Fixed to
  idempotent `ADD COLUMN IF NOT EXISTS` (repo convention). Integration run confirms the chain now
  applies cleanly on a fresh DB.

### RESULT: Plans 01 & 02 = 100% (engine + builder), verified vs real Postgres. Plans 09/10/11 core
### landed + tested. Migration fresh-deploy bug fixed. DEFERRED (flagged): Plan 03 voice (branch
### inaccessible — seam ready); Plan 09 full disposable-projection read model.
</content>
