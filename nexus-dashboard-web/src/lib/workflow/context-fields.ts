import type { TriggerType } from "@/types/workflow"

export interface WorkflowContextField {
    name: string
    label: string
    sample: unknown
    group: "payload"
    triggerTypes?: TriggerType[]
}

export const APPOINTMENT_CONTEXT_TRIGGERS: TriggerType[] = [
    "appointment_offset",
    "appointment_state_changed",
    "patient_status_changed",
]
const SMS_REPLY_CONTEXT_TRIGGERS: TriggerType[] = ["sms_reply"]
const RECALL_CONTEXT_TRIGGERS: TriggerType[] = ["recall_scan"]

export const WORKFLOW_CONTEXT_FIELDS: WorkflowContextField[] = [
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
    field("gotracker_appointment_id", "AppointmentId", "1343", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("gotracker_contact_id", "ContactId", "583", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("gotracker_provider_id", "ProviderId", "2", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("gotracker_schedule_column_id", "ScheduleColumnId", "1", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("is_preconfirmed", "IsPreconfirmed", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("is_confirmed", "IsConfirmed", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("master_id", "MasterId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_date", "AppointmentDate", "2026-07-30T00:00:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_time", "AppointmentTime", "16:15:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_duration", "Duration", "00:15:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("original_date", "OriginalDate", "2026-07-30T00:00:00", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_reason", "Reason", "bridge prep", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("detail", "Detail", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("appointment_amount", "AppointmentAmount", 0, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("is_recall", "IsRecall", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("is_personal", "IsPersonal", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("is_all_day_appointment", "IsAllDayAppointment", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("has_alarm", "HasAlarm", false, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("notify_time", "NotifyTime", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("gotracker_status_id", "StatusId", "1", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("check_in", "CheckIn", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("in_chair", "InChair", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("out_chair", "OutChair", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("check_out", "CheckOut", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("flow_state", "FlowState", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("flow_change", "FlowChange", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("comments", "Comments", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("booked_user_id", "BookedUserId", "Admin", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("booked_timestamp", "BookedTimeStamp", "2026-07-29T20:32:00.81", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("booked_machine_name", "BookedMachineName", "EC2AMAZ-QKGJ1Q1", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("created_user_id", "CreatedUserId", "Admin", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("created_timestamp", "CreatedTimeStamp", "2026-07-29T20:32:00.807", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("modified_user_id", "ModifiedUserId", "Admin", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("modified_timestamp", "ModifiedTimeStamp", "2026-07-29T20:32:00.807", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("modified_machine_name", "ModifiedMachineName", "EC2AMAZ-QKGJ1Q1", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("created_machine_name", "CreatedMachineName", "EC2AMAZ-QKGJ1Q1", "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("rebook_info", "RebookInfo", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("confirmed_timestamp", "ConfirmedTimeStamp", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("confirmed_user_id", "ConfirmedUserId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("confirmed_machine_name", "ConfirmedMachineName", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("rebook_id", "RebookId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("cancelled_timestamp", "CancelledTimeStamp", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("cancelled_user_id", "CancelledUserId", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
    field("cancelled_machine_name", "CancelledMachineName", null, "payload", APPOINTMENT_CONTEXT_TRIGGERS),
]

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

export function contextFieldsForTrigger(triggerType: TriggerType): WorkflowContextField[] {
    return WORKFLOW_CONTEXT_FIELDS.filter(
        (field) => !field.triggerTypes || field.triggerTypes.includes(triggerType),
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
): WorkflowContextField {
    return { name, label, sample, group, triggerTypes }
}

function pathParts(path: string): string[] {
    return path
        .replace(/\[/g, ".")
        .replace(/\]/g, "")
        .split(".")
        .map((part: string) => part.trim())
        .filter(Boolean)
}
