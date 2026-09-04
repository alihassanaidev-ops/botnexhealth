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
    "appointment_offset",
    "appointment_state_changed",
    "patient_status_changed",
]
const GOTRACKER = ["gotracker"] as const
const NEXHEALTH = ["nexhealth"] as const
const SMS_REPLY_CONTEXT_TRIGGERS: TriggerType[] = ["sms_reply"]
const RECALL_CONTEXT_TRIGGERS: TriggerType[] = ["recall_scan"]
const FORM_CONTEXT_TRIGGERS: TriggerType[] = ["form_submitted"]

export const WORKFLOW_CONTEXT_FIELDS: WorkflowContextField[] = [
    // The fixed part of a form submission's context. The answers themselves are
    // per-form, so they are offered from the trigger panel (which knows which
    // forms are selected) rather than listed here; the editor's "Custom field…"
    // entry accepts `form_answers.<key>` directly.
    field("form_provider", "Form provider", "typeform", "payload", FORM_CONTEXT_TRIGGERS),
    field("form_name", "Form name", "New Patient Enquiry", "payload", FORM_CONTEXT_TRIGGERS),
    field("form_id", "Form ID", "0b0c2f5e-1f2a-4c3d-9a1b-2c3d4e5f6a7b", "payload", FORM_CONTEXT_TRIGGERS),
    field("form_external_id", "Provider form ID", "AbC123", "payload", FORM_CONTEXT_TRIGGERS),
    field("form_created_contact", "Created a new contact", true, "payload", FORM_CONTEXT_TRIGGERS),
    field("matched_existing_contact", "Matched an existing patient", false, "payload", FORM_CONTEXT_TRIGGERS),
    field("sms_reply_body", "SMS reply body", "I need to reschedule", "payload", SMS_REPLY_CONTEXT_TRIGGERS),
    field("sms_reply_intent", "SMS reply intent", "free_text", "payload", SMS_REPLY_CONTEXT_TRIGGERS),
    field("sms_reply_message_sid", "SMS message SID", "SM123", "payload", SMS_REPLY_CONTEXT_TRIGGERS),
    field("inbound_sms_message_id", "Inbound SMS message ID", "inbound-1", "payload", SMS_REPLY_CONTEXT_TRIGGERS),
    field("recall_due_date", "Recall due date", "2026-08-15", "payload", RECALL_CONTEXT_TRIGGERS),
    field("recall_type_id", "Recall type ID", "nh-7", "payload", RECALL_CONTEXT_TRIGGERS),
    field("recall_type_name", "Recall type name", "Hygiene", "payload", RECALL_CONTEXT_TRIGGERS),
    field("recall_type", "Recall type", "Hygiene", "payload", RECALL_CONTEXT_TRIGGERS),
    field("recall_interval_months", "Recall interval months", 6, "payload", RECALL_CONTEXT_TRIGGERS),
    field("last_visit_date", "Last visit date", "2026-02-12", "payload", RECALL_CONTEXT_TRIGGERS),
    field("treatment_plan_statuses", "Treatment plan statuses", ["accepted"], "payload", RECALL_CONTEXT_TRIGGERS),
    field("active_treatment_plan_count", "Active treatment plan count", 1, "payload", RECALL_CONTEXT_TRIGGERS),
    field("has_active_treatment_plan", "Has active treatment plan", true, "payload", RECALL_CONTEXT_TRIGGERS),
    field("gotracker_appointment_id", "AppointmentId", "1343", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("gotracker_contact_id", "ContactId", "583", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("gotracker_provider_id", "ProviderId", "2", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("gotracker_schedule_column_id", "ScheduleColumnId", "1", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("is_preconfirmed", "IsPreconfirmed", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("is_confirmed", "IsConfirmed", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("master_id", "MasterId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("appointment_date", "AppointmentDate", "2026-07-30T00:00:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("appointment_time", "AppointmentTime", "16:15:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("appointment_duration", "Duration", "00:15:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("original_date", "OriginalDate", "2026-07-30T00:00:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("appointment_reason", "Reason", "bridge prep", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("detail", "Detail", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("appointment_amount", "AppointmentAmount", 0, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("is_recall", "IsRecall", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("is_personal", "IsPersonal", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("is_all_day_appointment", "IsAllDayAppointment", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("has_alarm", "HasAlarm", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("notify_time", "NotifyTime", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("gotracker_status_id", "StatusId", "1", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("check_in", "CheckIn", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("in_chair", "InChair", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("out_chair", "OutChair", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("check_out", "CheckOut", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("flow_state", "FlowState", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("flow_change", "FlowChange", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("comments", "Comments", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("booked_user_id", "BookedUserId", "Admin", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("booked_timestamp", "BookedTimeStamp", "2026-07-29T20:32:00.81", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("booked_machine_name", "BookedMachineName", "EC2AMAZ-QKGJ1Q1", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("created_user_id", "CreatedUserId", "Admin", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("created_timestamp", "CreatedTimeStamp", "2026-07-29T20:32:00.807", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("modified_user_id", "ModifiedUserId", "Admin", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("modified_timestamp", "ModifiedTimeStamp", "2026-07-29T20:32:00.807", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("modified_machine_name", "ModifiedMachineName", "EC2AMAZ-QKGJ1Q1", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("created_machine_name", "CreatedMachineName", "EC2AMAZ-QKGJ1Q1", "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("rebook_info", "RebookInfo", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("confirmed_timestamp", "ConfirmedTimeStamp", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("confirmed_user_id", "ConfirmedUserId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("confirmed_machine_name", "ConfirmedMachineName", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("rebook_id", "RebookId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("cancelled_timestamp", "CancelledTimeStamp", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("cancelled_user_id", "CancelledUserId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    field("cancelled_machine_name", "CancelledMachineName", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS, GOTRACKER),
    // NexHealth appointment context — the projection shape produced by the
    // backend's `appointment_trigger_service.get_appointment_context`.
    field("appointment_id", "Appointment ID", "987654", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("appointment_at", "Appointment start", "2026-07-30T16:15:00+00:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("appointment_start_time", "Appointment start time", "2026-07-30T16:15:00+00:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("appointment_status", "Appointment status", "booked", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("appointment_type_id", "Appointment type ID", "1001", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("appointment_type", "Appointment type", "Hygiene visit", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("appointment_type_name", "Appointment type name", "Hygiene visit", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("provider_id", "Provider ID", "377", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("patient_id", "Patient ID", "204012", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("contact_id", "Contact ID", "b7f0…", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
    field("location_id", "Location ID", "loc-1", "payload", APPOINTMENT_CONTEXT_TRIGGERS, NEXHEALTH),
]

/**
 * Sample NexHealth appointment context, mirroring the backend projection
 * shape from `appointment_trigger_service.get_appointment_context`.
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

export function contextFieldsForTrigger(
    triggerType: TriggerType,
    pmsType: string | null = null,
): WorkflowContextField[] {
    return WORKFLOW_CONTEXT_FIELDS.filter(
        (field) =>
            (!field.triggerTypes || field.triggerTypes.includes(triggerType)) &&
            // Unknown PMS (context loading): keep only PMS-neutral fields for
            // PMS-owned entries, so a NexHealth tenant never flashes GoTracker.
            (!field.pmsTypes || (pmsType !== null && field.pmsTypes.includes(pmsType))),
    )
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

function field(
    name: string,
    label: string,
    sample: unknown,
    group: WorkflowContextField["group"],
    triggerTypes?: TriggerType[],
    pmsTypes?: readonly string[],
): WorkflowContextField {
    return { name, label, sample, group, triggerTypes, pmsTypes }
}

function pathParts(path: string): string[] {
    return path
        .replace(/\[/g, ".")
        .replace(/\]/g, "")
        .split(".")
        .map((part: string) => part.trim())
        .filter(Boolean)
}
