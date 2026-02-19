# Retell Workflow Alignment: `lookup_patient` Basic -> Full Escalation

## Goal
Ensure call-start lookup is phone-first and HIPAA-minimized by default, then only request full context after verification.

## Required Retell Tool Contract
Tool: `lookup_patient(args)`

Supported args:
- `phone_number` (optional but expected on call start)
- `name` (optional)
- `email` (optional)
- `date_of_birth` (optional)
- `detail_level` (optional): `basic` | `full` (defaults to `basic`)

## Retell Agent Steps (Prompt/Workflow)
1. On call start, immediately invoke:
   - `lookup_patient({ phone_number: <caller_number>, detail_level: "basic" })`
2. If one or more matches are returned, perform identity verification in conversation before revealing sensitive details.
3. After verification, invoke:
   - `lookup_patient({ phone_number: <caller_number>, detail_level: "full" })`
   - Optionally include `name` and/or `date_of_birth` if collected.
4. Continue booking/reschedule/cancel flow only after verification is complete.

## Suggested Verification Script
- "I found your profile. For security, can you confirm your full name and date of birth?"
- If mismatch: do not escalate to `full`; proceed with callback/manual verification flow.

## Safety Notes
- `basic` responses return identity hints only (no full PHI context).
- Multi-match phone results should stay in disambiguation mode until verified.
- Do not request `detail_level=full` unless the caller has passed verification.
