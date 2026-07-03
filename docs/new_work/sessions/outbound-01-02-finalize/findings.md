# Findings — Finalize Plans 01 & 02

## Integration/exploration briefs (from planning)
- Dispatch convergence: 3 sites build WorkflowStepDispatcher; only inline route omits gate + hardcodes UTC.
  Celery tz block duplicated at tasks:175-179 & :317-321. Shared factory returns (dispatcher, tz).
- ComplianceGateService(session) — only dep is session; builds sub-services on demand.
- Quiet-hours: inline in compliance_gate_service.py:101-136, returns bool only; LocationOperatingHours
  has per-day open/close rows (models/location_operating_hours.py:15-51) → can compute next window.
- resume_after_timer assumes WaitNode (:197) → hold-resume needs advance() re-entry at same send node.
- Emergency halt: OutboundEmergencyHalt append-only; routes only insert/release rows, never touch runs/timers.
  cancel_timers_for_run (scheduler_service.py:104-122); cancel_run (enrollment_service.py:93).
- Dead-letter: capture_dead_letter (services/dead_letter.py:96) best-effort, own session. Workflow tasks
  don't call it today.
- SSE: publish_event (event_bus.py:99); MUST register type in _EVENT_SCHEMAS:53-58 (unknown raises).
- Metrics: pattern = scheduled boto3 CloudWatch script (scripts/publish_queue_metrics.py + test_queue_metrics.py).
- FE api convention: thin axios wrappers in lib/workflow-api.ts; no try/catch (pages toast). Missing
  listVersions/validateDefinition/listMergeFields. Endpoints exist: /versions:306 /validate:238 /merge-fields:267.
- Merge-field drift: FE MERGE_FIELDS vs backend STATIC_MERGE_FIELDS (template_renderer.py:54-91) disagree.
- FE tests: vitest, src/test/*.test.tsx, setup.ts stubs ResizeObserver/DOMMatrix for React Flow.

## Implementation log
(populated per phase)
</content>
