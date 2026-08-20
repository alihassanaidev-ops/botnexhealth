# NexHealth Reschedule V2 Research

Date: 2026-08-20
ClickUp: https://app.clickup.com/t/86eyp97cu

## Question

The existing NexHealth reschedule flow books a new appointment and then cancels
the old appointment because legacy NexHealth did not support changing an
appointment's start/end time in-place. The task is to plan a Retell-facing
`reschedule_appointment_v2` path that uses the stable NexHealth appointment
PATCH update when supported, while preserving the current fallback behavior.

## Primary-source findings

- NexHealth stable v3.0.0 is the current stable API version and is also
  published as `v20240412`; requests use the `Nex-Api-Version: v3.0.0` header.
  Source: https://docs.nexhealth.com/reference/introduction
- NexHealth's v2.2.2 `PATCH /appointments/{id}` page says rescheduling requires
  cancelling the original appointment and recreating it at the new time; start
  and end time cannot be updated there.
  Source: https://docs.nexhealth.com/v2.2.2/reference/patchappointmentsid
- NexHealth's stable `PATCH /appointments/{id}` page says `confirmed`,
  `cancelled`, `checkin_at`, `start_time`, `end_time`, `operatory_id`, and
  `note` can be patched, and that an existing appointment can be updated
  directly with `start_time`, `end_time`, `operatory_id`, and `note`.
  Source: https://docs.nexhealth.com/reference/patchappointmentsid
- The stable migration guide says v3.0.0 adds `start_time` + `end_time`
  reschedule fields, `operatory_id`, `note`, and `checkin_at` to
  `PATCH /appointments/{id}`. It also says `start_time` and `end_time` must be
  provided together.
  Source: https://docs.nexhealth.com/docs/api-v2-to-v20240412-migration-guide
- PMS support is limited. The current endpoint reference lists direct
  appointment update support for Dentrix, Dentrix Enterprise, Eaglesoft, and
  Open Dental. The migration guide also includes Denticon in that support list.
  This discrepancy should be handled conservatively in code comments and tests.
  Sources: https://docs.nexhealth.com/reference/patchappointmentsid and
  https://docs.nexhealth.com/docs/api-v2-to-v20240412-migration-guide
- Unsupported EHRs reject direct appointment-update field changes with HTTP 400
  and a message equivalent to "EMR does not support appointment update."
  Source: https://docs.nexhealth.com/docs/api-v2-to-v20240412-migration-guide

## Repo findings

- Retell currently registers `reschedule_appointment` in
  `src/app/retell/handlers.py`. It builds a `BookingRequest` and calls
  `ctx.adapter.reschedule_appointment(...)`.
- Retell idempotency currently wraps `reschedule_appointment`; a new mutating
  tool name such as `reschedule_appointment_v2` must be added to
  `IDEMPOTENT_FUNCTIONS` in `src/app/retell/idempotency.py`.
- NexHealth currently implements `reschedule_appointment` in
  `src/app/pms/nexhealth/adapter.py` by booking first and then cancelling the old
  appointment. The docs in `docs/NEXHEALTH.md` describe that as the current
  safety behavior.
- NexHealth API contract selection already exists in
  `src/app/nexhealth/api_contract.py`; only `stable_v3` can use direct PATCH
  reschedule fields.
- `PmsCapabilityService` has a broad `appointment_writeback` capability tied to
  the supported-API matrix's `Edit Appointment` row, but that row is too coarse
  for this task because it also covers confirmation/cancellation/check-in. Direct
  reschedule support needs a specific allowlist or capability.

## Planning implications

1. Keep the existing `reschedule_appointment` flow as the compatibility path.
2. Add a v2 Retell handler name, likely `reschedule_appointment_v2`, so Retell
   can be migrated intentionally without changing the existing tool contract.
3. In the NexHealth adapter, add a direct PATCH path that is used only when:
   the adapter contract is `stable_v3`, `slot_end` is known or can be computed,
   and the location's underlying PMS is in the direct-update supported set.
4. For all other cases, fall back to the current book-new-then-cancel-old flow.
5. Do not rely on a failed PATCH 400 as the primary capability detector if we can
   determine the PMS from `nexhealth_sync_statuses`; using 400 only as a final
   fallback risks making two writes if we subsequently book/cancel.
6. Retell-side changes are needed only in the dashboard/tool schema and backend
   handler registration/idempotency. No Retell provisioning code exists in this
   repo, so the agent tool JSON must be updated manually in Retell.
