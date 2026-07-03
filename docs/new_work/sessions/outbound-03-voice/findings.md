# Findings: Outbound 03 — Outbound Voice

## Agent JSON survey (ScaleNexusAI Main Agent (1)(1).json, project root)
- `channel: voice`, single enterprise agent, `response_engine: retell-llm` (llm_6fd5803216f5916518a7f7e08c69)
- Dynamic variables the prompt reads: **`first_name`**, **`user_number`** (patient phone),
  plus Retell auto-injected `current_time_*`, `call_id`, `transfer_number`.
  → Outbound call must pass `retell_llm_dynamic_variables = {first_name, user_number}`.
- 12 tools incl. lookup_patient, book_appointment, find_appointment_slots, reschedule/cancel —
  all function-call URLs at `api.staging.scalenexus.ai/api/v1/retell/functions?name=...`.
  These fire identically on outbound calls (agent-driven), no change needed.
- `webhook_events: [call_analyzed, call_ended, call_started]` — already subscribed.

## Existing infra (verified)
- `SendVoiceNode` (definition_schema.py L129): fields id, type, **retell_agent_id (REQUIRED, min_length=1)**,
  next_node_id, respect_quiet_hours=True, max_attempts (1-3, default 1).
- `InstitutionLocation`: has `retell_agent_id`, `twilio_from_number`, `phone`. NO retell from-number → build it.
- `Call` model: has `retell_call_id` (UNIQUE), `call_direction`, `agent_used`, `contact_id`, `location_id`.
  Required non-null `retention_class`, `retain_until` → Call rows go through webhook, not executor.
  NO `workflow_run_id` column → wait-for-outcome would need one (deferred).
- `settings.retell_api_secret` present (config.py L79).
- **Webhook `webhooks.py` L493-502 already branches on direction**: outbound uses `to_number` as patient
  phone. `saved_call` persisted with retention. → executor must NOT create Call row (would collide on
  UNIQUE retell_call_id + duplicate retention logic).

## Retell create-phone-call API (v2)
```
POST https://api.retellai.com/v2/create-phone-call
Authorization: Bearer <retell_api_secret>
{
  "from_number": "+1...",          # must be imported into Retell → retell_from_number
  "to_number": "+1...",            # contact.phone
  "override_agent_id": "agent_...",# node.retell_agent_id (else uses agent bound to from_number)
  "retell_llm_dynamic_variables": {"first_name": "...", "user_number": "+1..."},
  "metadata": {"workflow_run_id": "..."}   # correlation hedge for future wait-for-outcome
}
→ 201/200 with {"call_id": "..."}
```

## Design conclusion
Executor mirrors SmsNodeExecutor but is simpler: no SmsService, no Call write.
Just: resolve contact.phone + location.retell_from_number → POST create-phone-call →
complete_step("call_placed") / fail on error. Webhook handles all recording.

## Alembic
- Head: `20260703_provisioning` (20260703_institution_provisioning.py). New migration chains from it.
- Keep revision id <= 32 chars (prior lesson): use `20260703_retell_from_number` (27 chars) ✓
