# Progress Log

## Session: Dental outbound workflow primitives

- **Status:** implementation complete; provider-side phone connect remains blocked by international dialing permission.
- Added appointment-type-aware appointment-offset enrollment so campaigns can target clinic appointment types such as surgery/major treatment.
- Added appointment-relative wait support so workflows can wait until a configured time before or after the appointment time.
- Added `contains` / `not_contains` condition operators for simple dental keyword/status branching.
- Added an `update_patient_status` workflow action backed by local `patient_workflow_status_events` history.
- Added a surgery confirmation + post-op voice template using Retell outcome waiting.
- Updated the workflow builder to configure appointment type filters, appointment-relative waits, condition operators, and status-update nodes.
- Ran local ngrok Retell QA using a disposable local workflow and fake appointment trigger.

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Backend focused pytest | Automation dispatcher, Plan 09 trigger matching, campaign template tests | New schema/runtime paths pass | 81 passed | passed |
| Frontend workflow tests | Workflow builder/test-run/publish vitest | Builder supports new controls | Passed after increasing two slow publish test timeouts | passed |
| Frontend build | `npm run build` | Production build succeeds | Passed with normal chunk/Browserslist warnings | passed |
| Backend lint | Ruff on touched backend/tests | No lint issues | Passed | passed |
| Alembic smoke | Local Docker `alembic upgrade head` | New status table migration applies | Passed after making migration idempotent for existing local table | passed |
| Combined local regression | GoTracker/NexHealth/automation focused pytest | Existing PMS adapters and workflow paths still pass | 126 passed | passed |
| Retell local QA | Fake appointment trigger, local worker, ngrok Retell webhook, test agent | Workflow calls Retell, receives webhook, resumes run | Passed through app/Retell/webhook resume; call failed at provider with `No International Permission` for Pakistan destination | partial |

## Notes

- The Retell app integration path is proven locally: workflow enrollment, `send_voice`, Retell `201 Created`, Retell webhook receipt, call record save, and voice outcome resume all ran.
- The remaining blocked part is not application code: Retell/Twilio returned SIP 403 `No International Permission` when dialing the Pakistan test number.
- `update_patient_status` records ScaleNexus local journey/status history only; it does not write status back into a PMS.
