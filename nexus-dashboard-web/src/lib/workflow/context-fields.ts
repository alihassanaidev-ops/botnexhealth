import type { TriggerType } from "@/types/workflow"

export interface WorkflowContextField {
    name: string
    label: string
    sample: unknown
    group: "payload"
    triggerTypes?: TriggerType[]
    /**
     * PMS types whose runtime actually provides this field. Absent = every
     * PMS. The GoTracker webhook payload fields only exist on GoTracker
     * locations; NexHealth appointment context is the projection shape built
     * by `appointment_trigger_service.get_appointment_context`.
     */
    pmsTypes?: readonly string[]
}

export const APPOINTMENT_CONTEXT_TRIGGERS: TriggerType[] = [
    "event",
    "internal_status",
]

/*
 * The per-trigger field list that used to live here is gone.
 *
 * It was one of six competing vocabularies: it offered `appointment_at` while
 * the message editor offered `{{appointment_datetime}}` and the trigger picker
 * offered `appointment.start_at`, and nothing kept the three honest. Field
 * discovery now comes from the served event catalog — see
 * `lib/workflow/event-catalog.ts` — so every panel names a field the same way
 * and the backend decides what a given practice software can actually supply.
 *
 * What remains below is the raw incoming-payload preview and its formatters.
 * That is a genuinely different thing: it shows the author what the practice
 * software really sent, which is the escape hatch for values the canonical
 * vocabulary does not cover.
 */

export const NEXHEALTH_APPOINTMENT_CONTEXT_SAMPLE = {
    data: {
        appointment_id: "987654",
        appointment_at: "2026-07-30T16:15:00+00:00",
        appointment_start_time: "2026-07-30T16:15:00+00:00",
        appointment_status: "booked",
        appointment_reason: "Hygiene visit",
        appointment_type_id: "1001",
        appointment_type: "Hygiene visit",
        appointment_type_name: "Hygiene visit",
        provider_id: "377",
        patient_id: "204012",
        contact_id: "b7f04c1e-2d0f-4a57-9d8f-000000000000",
        location_id: "loc-1",
    },
}

export const GOTRACKER_APPOINTMENT_WEBHOOK_SAMPLE = {
    data: {
        AppointmentId: 1343,
        ContactId: 583,
        ProviderId: 2,
        ScheduleColumnId: 1,
        IsPreconfirmed: false,
        IsConfirmed: false,
        MasterId: null,
        AppointmentDate: "2026-07-30T00:00:00",
        AppointmentTime: "16:15:00",
        Duration: "00:15:00",
        OriginalDate: "2026-07-30T00:00:00",
        Reason: "bridge prep",
        Detail: null,
        AppointmentAmount: 0,
        IsRecall: false,
        IsPersonal: false,
        IsAllDayAppointment: false,
        HasAlarm: false,
        NotifyTime: null,
        StatusId: 1,
        CheckIn: null,
        InChair: null,
        OutChair: null,
        CheckOut: null,
        FlowState: null,
        FlowChange: null,
        Comments: null,
        BookedUserId: "Admin",
        BookedTimeStamp: "2026-07-29T20:32:00.81",
        BookedMachineName: "EC2AMAZ-QKGJ1Q1",
        CreatedUserId: "Admin",
        CreatedTimeStamp: "2026-07-29T20:32:00.807",
        ModifiedUserId: "Admin",
        ModifiedTimeStamp: "2026-07-29T20:32:00.807",
        ModifiedMachineName: "EC2AMAZ-QKGJ1Q1",
        CreatedMachineName: "EC2AMAZ-QKGJ1Q1",
        RebookInfo: null,
        ConfirmedTimeStamp: null,
        ConfirmedUserId: null,
        ConfirmedMachineName: null,
        RebookId: null,
        CancelledTimeStamp: null,
        CancelledUserId: null,
        CancelledMachineName: null,
    },
}

export const SAMPLE_WORKFLOW_CONTEXT: Record<string, unknown> = {
    form_provider: "typeform",
    form_name: "New Patient Enquiry",
    form_id: "0b0c2f5e-1f2a-4c3d-9a1b-2c3d4e5f6a7b",
    form_external_id: "AbC123",
    form_created_contact: true,
    matched_existing_contact: false,
    form_answers: { problem: "Toothache", visited_before: false },
    event: "appointment.created",
    source: "gotracker",
    inbound_sms_message_id: "inbound-1",
    sms_reply_message_sid: "SM123",
    sms_reply_body: "I need to reschedule",
    sms_reply_intent: "free_text",
    recall_due_date: "2026-08-15",
    recall_type_id: "nh-7",
    recall_type_name: "Hygiene",
    recall_type: "Hygiene",
    recall_interval_months: 6,
    last_visit_date: "2026-02-12",
    treatment_plan_statuses: ["accepted"],
    active_treatment_plan_count: 1,
    has_active_treatment_plan: true,
    gotracker_appointment_id: "1343",
    gotracker_contact_id: "583",
    contact_id: "gt-583",
    is_preconfirmed: false,
    is_confirmed: false,
    master_id: null,
    appointment_reason: "bridge prep",
    appointment_reasons: ["bridge prep"],
    gotracker_reasons: ["bridge prep"],
    appointment_status: "booked",
    appointment_status_id: "1",
    gotracker_status_id: "1",
    appointment_date: "2026-07-30T00:00:00",
    appointment_time: "16:15:00",
    appointment_duration: "00:15:00",
    original_date: "2026-07-30T00:00:00",
    detail: null,
    appointment_amount: 0,
    is_recall: false,
    is_personal: false,
    is_all_day_appointment: false,
    has_alarm: false,
    notify_time: null,
    check_in: null,
    in_chair: null,
    out_chair: null,
    check_out: null,
    flow_state: null,
    flow_change: null,
    comments: null,
    provider_id: "gt-2",
    gotracker_provider_id: "2",
    schedule_column_id: "1",
    gotracker_schedule_column_id: "1",
    booked_user_id: "Admin",
    booked_timestamp: "2026-07-29T20:32:00.81",
    booked_machine_name: "EC2AMAZ-QKGJ1Q1",
    created_user_id: "Admin",
    created_timestamp: "2026-07-29T20:32:00.807",
    modified_user_id: "Admin",
    modified_timestamp: "2026-07-29T20:32:00.807",
    modified_machine_name: "EC2AMAZ-QKGJ1Q1",
    created_machine_name: "EC2AMAZ-QKGJ1Q1",
    rebook_info: null,
    confirmed_timestamp: null,
    confirmed_user_id: null,
    confirmed_machine_name: null,
    rebook_id: null,
    cancelled_timestamp: null,
    cancelled_user_id: null,
    cancelled_machine_name: null,
    gotracker_payload: {
        event: "appointment.created",
        data: GOTRACKER_APPOINTMENT_WEBHOOK_SAMPLE.data,
        appointment: {
            id: "1343",
            contact_id: "583",
            is_preconfirmed: false,
            is_confirmed: false,
            master_id: null,
            date: "2026-07-30T00:00:00",
            time: "16:15:00",
            reasons: ["bridge prep"],
            original_date: "2026-07-30T00:00:00",
            detail: null,
            appointment_amount: 0,
            is_recall: false,
            is_personal: false,
            is_all_day_appointment: false,
            has_alarm: false,
            notify_time: null,
            status_id: "1",
            check_in: null,
            in_chair: null,
            out_chair: null,
            check_out: null,
            flow_state: null,
            flow_change: null,
            comments: null,
            provider_id: "2",
            schedule_column_id: "1",
            duration: "00:15:00",
            booked_user_id: "Admin",
            booked_timestamp: "2026-07-29T20:32:00.81",
            booked_machine_name: "EC2AMAZ-QKGJ1Q1",
            created_user_id: "Admin",
            created_timestamp: "2026-07-29T20:32:00.807",
            modified_user_id: "Admin",
            modified_timestamp: "2026-07-29T20:32:00.807",
            modified_machine_name: "EC2AMAZ-QKGJ1Q1",
            created_machine_name: "EC2AMAZ-QKGJ1Q1",
            rebook_info: null,
            confirmed_timestamp: null,
            confirmed_user_id: null,
            confirmed_machine_name: null,
            rebook_id: null,
            cancelled_timestamp: null,
            cancelled_user_id: null,
            cancelled_machine_name: null,
        },
    },
}

/**
 * NexHealth flavor of the sample context used for json_mapper path previews:
 * the shared (form/SMS/recall) keys plus the NexHealth appointment projection,
 * with none of the GoTracker webhook payload.
 */
export const NEXHEALTH_SAMPLE_WORKFLOW_CONTEXT: Record<string, unknown> = {
    form_provider: SAMPLE_WORKFLOW_CONTEXT.form_provider,
    form_name: SAMPLE_WORKFLOW_CONTEXT.form_name,
    form_id: SAMPLE_WORKFLOW_CONTEXT.form_id,
    form_external_id: SAMPLE_WORKFLOW_CONTEXT.form_external_id,
    form_created_contact: SAMPLE_WORKFLOW_CONTEXT.form_created_contact,
    matched_existing_contact: SAMPLE_WORKFLOW_CONTEXT.matched_existing_contact,
    form_answers: SAMPLE_WORKFLOW_CONTEXT.form_answers,
    inbound_sms_message_id: SAMPLE_WORKFLOW_CONTEXT.inbound_sms_message_id,
    sms_reply_message_sid: SAMPLE_WORKFLOW_CONTEXT.sms_reply_message_sid,
    sms_reply_body: SAMPLE_WORKFLOW_CONTEXT.sms_reply_body,
    sms_reply_intent: SAMPLE_WORKFLOW_CONTEXT.sms_reply_intent,
    recall_due_date: SAMPLE_WORKFLOW_CONTEXT.recall_due_date,
    recall_type_id: SAMPLE_WORKFLOW_CONTEXT.recall_type_id,
    recall_type_name: SAMPLE_WORKFLOW_CONTEXT.recall_type_name,
    recall_type: SAMPLE_WORKFLOW_CONTEXT.recall_type,
    recall_interval_months: SAMPLE_WORKFLOW_CONTEXT.recall_interval_months,
    last_visit_date: SAMPLE_WORKFLOW_CONTEXT.last_visit_date,
    treatment_plan_statuses: SAMPLE_WORKFLOW_CONTEXT.treatment_plan_statuses,
    active_treatment_plan_count: SAMPLE_WORKFLOW_CONTEXT.active_treatment_plan_count,
    has_active_treatment_plan: SAMPLE_WORKFLOW_CONTEXT.has_active_treatment_plan,
    ...NEXHEALTH_APPOINTMENT_CONTEXT_SAMPLE.data,
    appointment: {
        id: "987654",
        start_time: "2026-07-30T16:15:00+00:00",
        status: "booked",
        reason: "Hygiene visit",
        appointment_type_id: "1001",
        appointment_type_name: "Hygiene visit",
        provider_id: "377",
    },
}

/** The PMS-appropriate sample context for path previews and dry-run seeds. */
export function sampleWorkflowContext(pmsType: string | null): Record<string, unknown> {
    return pmsType === "nexhealth" ? NEXHEALTH_SAMPLE_WORKFLOW_CONTEXT : SAMPLE_WORKFLOW_CONTEXT
}

export function contextValueAtPath(context: Record<string, unknown>, path: string): unknown {
    if (path in context) return context[path]

    let current: unknown = context
    for (const part of pathParts(path)) {
        if (current && typeof current === "object" && !Array.isArray(current)) {
            current = (current as Record<string, unknown>)[part]
        } else if (Array.isArray(current) && /^\d+$/.test(part)) {
            current = current[Number(part)]
        } else {
            return undefined
        }
        if (current === undefined || current === null) return current
    }
    return current
}

export function formatContextValue(value: unknown): string {
    if (value === undefined) return "missing"
    if (value === null) return "null"
    if (typeof value === "string") return value
    return JSON.stringify(value)
}


function pathParts(path: string): string[] {
    return path
        .replace(/\[/g, ".")
        .replace(/\]/g, "")
        .split(".")
        .map((part: string) => part.trim())
        .filter(Boolean)
}
