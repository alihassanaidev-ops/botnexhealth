/**
 * Calls API service.
 *
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { CallDetail, CallRecord, CallsListResponse, TranscriptTurn } from "@/types";

const USE_MOCK_DATA = true

export interface CallsFilters {
    limit?: number;
    offset?: number;
    /** Single primary-status shorthand */
    status?: string;
    /** Multi-tag filter — each tag must be present in the call's tags */
    tags?: string[];
    direction?: string;
    search?: string;
    date_from?: string;
    date_to?: string;
}

const MOCK_CALLS: CallRecord[] = [
    {
        id: "call-001",
        call_direction: "inbound",
        call_status: "appointment_booked",
        call_tags: ["appointment_booked"],
        patient_status: "existing",
        summary: "Patient called to schedule a routine cleaning appointment for next week.",
        patient_sentiment: "Positive",
        next_action: null,
        is_new_patient: false,
        is_complaint: false,
        is_insurance_billing: false,
        call_date: "2026-03-12",
        call_time: "10:30:00",
        call_duration_seconds: 245,
        callback_resolved: false,
        created_at: "2026-03-12T10:30:00Z",
        contact: { id: "contact-1", full_name: "John Smith", first_name: "John", last_name: "Smith" },
    },
    {
        id: "call-002",
        call_direction: "inbound",
        call_status: "needs_callback",
        call_tags: ["needs_callback"],
        patient_status: "new",
        summary: "Patient needs callback regarding insurance verification for a crown procedure.",
        patient_sentiment: "Neutral",
        next_action: "Callback patient after verifying insurance coverage",
        is_new_patient: true,
        is_complaint: false,
        is_insurance_billing: true,
        call_date: "2026-03-12",
        call_time: "11:15:00",
        call_duration_seconds: 120,
        callback_resolved: false,
        created_at: "2026-03-12T11:15:00Z",
        contact: { id: "contact-2", full_name: "Sarah Johnson", first_name: "Sarah", last_name: "Johnson" },
    },
    {
        id: "call-003",
        call_direction: "inbound",
        call_status: "appointment_cancelled",
        call_tags: ["appointment_cancelled"],
        patient_status: "existing",
        summary: "Patient called to cancel their appointment due to a work emergency.",
        patient_sentiment: "Negative",
        next_action: null,
        is_new_patient: false,
        is_complaint: false,
        is_insurance_billing: false,
        call_date: "2026-03-11",
        call_time: "14:20:00",
        call_duration_seconds: 95,
        callback_resolved: true,
        created_at: "2026-03-11T14:20:00Z",
        contact: { id: "contact-3", full_name: "Michael Brown", first_name: "Michael", last_name: "Brown" },
    },
    {
        id: "call-004",
        call_direction: "outbound",
        call_status: "appointment_rescheduled",
        call_tags: ["appointment_rescheduled"],
        patient_status: "existing",
        summary: "Called patient to confirm appointment and rescheduled to a different time.",
        patient_sentiment: "Positive",
        next_action: null,
        is_new_patient: false,
        is_complaint: false,
        is_insurance_billing: false,
        call_date: "2026-03-11",
        call_time: "09:45:00",
        call_duration_seconds: 180,
        callback_resolved: true,
        created_at: "2026-03-11T09:45:00Z",
        contact: { id: "contact-4", full_name: "Emily Davis", first_name: "Emily", last_name: "Davis" },
    },
    {
        id: "call-005",
        call_direction: "inbound",
        call_status: "faq_handled",
        call_tags: ["faq_handled"],
        patient_status: "new",
        summary: "Patient asked about office hours and accepted new patient consultation.",
        patient_sentiment: "Neutral",
        next_action: "Schedule new patient appointment",
        is_new_patient: true,
        is_complaint: false,
        is_insurance_billing: false,
        call_date: "2026-03-10",
        call_time: "16:00:00",
        call_duration_seconds: 65,
        callback_resolved: false,
        created_at: "2026-03-10T16:00:00Z",
        contact: { id: "contact-5", full_name: "Robert Wilson", first_name: "Robert", last_name: "Wilson" },
    },
    {
        id: "call-006",
        call_direction: "inbound",
        call_status: "emergency",
        call_tags: ["emergency"],
        patient_status: "existing",
        summary: "Patient called with dental emergency - severe tooth pain. Scheduled emergency appointment.",
        patient_sentiment: "Negative",
        next_action: "Emergency appointment scheduled for today",
        is_new_patient: false,
        is_complaint: false,
        is_insurance_billing: false,
        call_date: "2026-03-10",
        call_time: "08:30:00",
        call_duration_seconds: 320,
        callback_resolved: false,
        created_at: "2026-03-10T08:30:00Z",
        contact: { id: "contact-6", full_name: "Jennifer Martinez", first_name: "Jennifer", last_name: "Martinez" },
    },
    {
        id: "call-007",
        call_direction: "inbound",
        call_status: "financial_inquiry",
        call_tags: ["financial_inquiry"],
        patient_status: "existing",
        summary: "Patient asked about payment plans and accepted financing option.",
        patient_sentiment: "Neutral",
        next_action: null,
        is_new_patient: false,
        is_complaint: false,
        is_insurance_billing: true,
        call_date: "2026-03-09",
        call_time: "13:45:00",
        call_duration_seconds: 210,
        callback_resolved: true,
        created_at: "2026-03-09T13:45:00Z",
        contact: { id: "contact-7", full_name: "David Anderson", first_name: "David", last_name: "Anderson" },
    },
    {
        id: "call-008",
        call_direction: "inbound",
        call_status: "complaint",
        call_tags: ["complaint"],
        patient_status: "existing",
        summary: "Patient complained about wait time during last visit. Offered Apology.",
        patient_sentiment: "Negative",
        next_action: "Follow up with office manager about wait times",
        is_new_patient: false,
        is_complaint: true,
        is_insurance_billing: false,
        call_date: "2026-03-09",
        call_time: "10:00:00",
        call_duration_seconds: 180,
        callback_resolved: false,
        created_at: "2026-03-09T10:00:00Z",
        contact: { id: "contact-8", full_name: "Lisa Thomas", first_name: "Lisa", last_name: "Thomas" },
    },
    {
        id: "call-009",
        call_direction: "outbound",
        call_status: "appointment_booked",
        call_tags: ["appointment_booked"],
        patient_status: "new",
        summary: "Called to confirm new patient appointment. Appointment confirmed.",
        patient_sentiment: "Positive",
        next_action: null,
        is_new_patient: true,
        is_complaint: false,
        is_insurance_billing: false,
        call_date: "2026-03-08",
        call_time: "15:30:00",
        call_duration_seconds: 150,
        callback_resolved: true,
        created_at: "2026-03-08T15:30:00Z",
        contact: { id: "contact-9", full_name: "Chris Garcia", first_name: "Chris", last_name: "Garcia" },
    },
    {
        id: "call-010",
        call_direction: "inbound",
        call_status: "transferred",
        call_tags: ["transferred"],
        patient_status: "existing",
        summary: "Patient transferred to billing department for insurance question.",
        patient_sentiment: "Neutral",
        next_action: null,
        is_new_patient: false,
        is_complaint: false,
        is_insurance_billing: true,
        call_date: "2026-03-08",
        call_time: "12:00:00",
        call_duration_seconds: 90,
        callback_resolved: true,
        created_at: "2026-03-08T12:00:00Z",
        contact: { id: "contact-10", full_name: "Amanda White", first_name: "Amanda", last_name: "White" },
    },
]

const MOCK_CALL_DETAILS: Record<string, CallDetail> = {
    "call-001": {
        ...MOCK_CALLS[0],
        transcript: "Agent: Hello, thank you for calling Smile Dental. How can I help you today?\n\nPatient: Hi, I'd like to schedule a cleaning appointment.\n\nAgent: Of course! We have availability next week. Would Monday or Tuesday work better for you?\n\nPatient: Tuesday at 10 AM works.\n\nAgent: Great! I've scheduled your cleaning for Tuesday, March 17th at 10:00 AM. Is this your first time with us?\n\nPatient: No, I've been here before.\n\nAgent: Perfect! We'll see you then. Is there anything else I can help you with?\n\nPatient: That's all, thank you.\n\nAgent: Thank you for calling! Have a great day!",
        transcript_with_tool_calls: [
            { role: "agent", content: "Hello, thank you for calling Smile Dental. How can I help you today?" },
            { role: "user", content: "Hi, I'd like to schedule a cleaning appointment." },
            { role: "agent", content: "Of course! We have availability next week. Would Monday or Tuesday work better for you?" },
            { role: "user", content: "Tuesday at 10 AM works." },
            { role: "tool_call_invocation", name: "create_appointment", arguments: "{\"appointment_type\": \"cleaning\", \"date\": \"2026-03-17\", \"time\": \"10:00\"}" },
            { role: "tool_call_result", name: "create_appointment", content: "Appointment created successfully. Confirmation #12345" },
            { role: "agent", content: "Great! I've scheduled your cleaning for Tuesday, March 17th at 10:00 AM. Is this your first time with us?" },
            { role: "user", content: "No, I've been here before." },
            { role: "agent", content: "Perfect! We'll see you then. Is there anything else I can help you with?" },
            { role: "user", content: "That's all, thank you." },
            { role: "agent", content: "Thank you for calling! Have a great day!" },
        ] as TranscriptTurn[],
        scrubbed_transcript_with_tool_calls: [
            { role: "agent", content: "Hello, thank you for calling [CLINIC]. How can I help you today?" },
            { role: "user", content: "Hi, I'd like to schedule a cleaning appointment." },
            { role: "agent", content: "Of course! We have availability next week. Would Monday or Tuesday work better for you?" },
            { role: "user", content: "Tuesday at 10 AM works." },
            { role: "tool_call_invocation", name: "create_appointment", arguments: "{\"appointment_type\": \"cleaning\", \"date\": \"2026-03-17\", \"time\": \"10:00\"}" },
            { role: "tool_call_result", name: "create_appointment", content: "Appointment created successfully. Confirmation #12345" },
            { role: "agent", content: "Great! I've scheduled your cleaning for Tuesday, March 17th at 10:00 AM. Is this your first time with us?" },
            { role: "user", content: "No, I've been here before." },
            { role: "agent", content: "Perfect! We'll see you then. Is there anything else I can help you with?" },
            { role: "user", content: "That's all, thank you." },
            { role: "agent", content: "Thank you for calling! Have a great day!" },
        ] as TranscriptTurn[],
        recording_url: "https://example.com/recordings/call-001.mp3",
        custom_fields: [
            { field_key: "appointment_type", field_name: "Appointment Type", field_type: "dropdown", value: "Cleaning", is_phi: false, display_order: 1 },
            { field_key: "appointment_date", field_name: "Appointment Date", field_type: "date", value: "2026-03-17", is_phi: false, display_order: 2 },
        ],
    },
    "call-002": {
        ...MOCK_CALLS[1],
        transcript: "Agent: Hello, thank you for calling Smile Dental. How can I help you today?\n\nPatient: Hi, I'm a new patient and I need to get a crown done. I wanted to check if you accept my insurance.\n\nAgent: I'd be happy to help you with that! Could you please provide your insurance information?\n\nPatient: It's Delta Dental.\n\nAgent: Thank you. Let me check your coverage. It looks like we do accept Delta Dental. Would you like to schedule a consultation appointment?\n\nPatient: Yes, please. But I'm not sure about the cost.\n\nAgent: I understand. Let me have our billing team call you back with the cost breakdown. Would that work?\n\nPatient: Yes, that would be great. My number is 555-0123.\n\nAgent: Perfect! Someone will call you within the next 24 hours. Is there anything else I can help you with?\n\nPatient: No, that's all. Thank you!",
        transcript_with_tool_calls: [
            { role: "agent", content: "Hello, thank you for calling Smile Dental. How can I help you today?" },
            { role: "user", content: "Hi, I'm a new patient and I need to get a crown done. I wanted to check if you accept my insurance." },
            { role: "tool_call_invocation", name: "verify_insurance", arguments: "{\"insurance_provider\": \"Delta Dental\"}" },
            { role: "tool_call_result", name: "verify_insurance", content: "Insurance verified. Delta Dental is accepted at this location." },
            { role: "agent", content: "I'd be happy to help you with that! We do accept Delta Dental. Would you like to schedule a consultation appointment?" },
            { role: "user", content: "Yes, please. But I'm not sure about the cost." },
            { role: "agent", content: "I understand. Let me have our billing team call you back with the cost breakdown. Would that work?" },
            { role: "user", content: "Yes, that would be great. My number is 555-0123." },
            { role: "tool_call_invocation", name: "create_callback_request", arguments: "{\"reason\": \"insurance_cost_inquiry\", \"phone\": \"555-0123\"}" },
            { role: "tool_call_result", name: "create_callback_request", content: "Callback request created. Reference #CB-9876" },
            { role: "agent", content: "Perfect! Someone will call you within the next 24 hours. Is there anything else I can help you with?" },
            { role: "user", content: "No, that's all. Thank you!" },
        ] as TranscriptTurn[],
        scrubbed_transcript_with_tool_calls: [
            { role: "agent", content: "Hello, thank you for calling [CLINIC]. How can I help you today?" },
            { role: "user", content: "Hi, I'm a new patient and I need to get a crown done. I wanted to check if you accept my insurance." },
            { role: "tool_call_invocation", name: "verify_insurance", arguments: "{\"insurance_provider\": \"[INSURANCE]\"}" },
            { role: "tool_call_result", name: "verify_insurance", content: "Insurance verified. [INSURANCE] is accepted at this location." },
            { role: "agent", content: "I'd be happy to help you with that! We do accept [INSURANCE]. Would you like to schedule a consultation appointment?" },
            { role: "user", content: "Yes, please. But I'm not sure about the cost." },
            { role: "agent", content: "I understand. Let me have our billing team call you back with the cost breakdown. Would that work?" },
            { role: "user", content: "Yes, that would be great. My number is [PHONE]." },
            { role: "tool_call_invocation", name: "create_callback_request", arguments: "{\"reason\": \"insurance_cost_inquiry\", \"phone\": \"[PHONE]\"}" },
            { role: "tool_call_result", name: "create_callback_request", content: "Callback request created. Reference #CB-9876" },
            { role: "agent", content: "Perfect! Someone will call you within the next 24 hours. Is there anything else I can help you with?" },
            { role: "user", content: "No, that's all. Thank you!" },
        ] as TranscriptTurn[],
        recording_url: "https://example.com/recordings/call-002.mp3",
        custom_fields: [
            { field_key: "insurance_provider", field_name: "Insurance Provider", field_type: "dropdown", value: "Delta Dental", is_phi: false, display_order: 1 },
            { field_key: "procedure_type", field_name: "Procedure Type", field_type: "dropdown", value: "Crown", is_phi: false, display_order: 2 },
            { field_key: "callback_requested", field_name: "Callback Requested", field_type: "boolean", value: "true", is_phi: false, display_order: 3 },
        ],
    },
    "call-003": {
        ...MOCK_CALLS[2],
        transcript: "Agent: Hello, thank you for calling Smile Dental. How can I help you today?\n\nPatient: Hi, I need to cancel my appointment that was scheduled for tomorrow.\n\nAgent: I'm sorry to hear that. May I ask the reason for the cancellation?\n\nPatient: I have a work emergency and won't be able to make it.\n\nAgent: I completely understand. I'll cancel your appointment. Would you like to reschedule for another time?\n\nPatient: I'm not sure when I'll be available. I'll call back later to reschedule.\n\nAgent: No problem! When you're ready, just give us a call and we'll find a time that works for you.\n\nPatient: Thank you.\n\nAgent: Thank you for letting us know. Have a good day!",
        transcript_with_tool_calls: [
            { role: "agent", content: "Hello, thank you for calling Smile Dental. How can I help you today?" },
            { role: "user", content: "Hi, I need to cancel my appointment that was scheduled for tomorrow." },
            { role: "agent", content: "I'm sorry to hear that. May I ask the reason for the cancellation?" },
            { role: "user", content: "I have a work emergency and won't be able to make it." },
            { role: "tool_call_invocation", name: "cancel_appointment", arguments: "{\"appointment_id\": \"APT-12345\", \"reason\": \"work_emergency\"}" },
            { role: "tool_call_result", name: "cancel_appointment", content: "Appointment cancelled successfully." },
            { role: "agent", content: "I completely understand. I've cancelled your appointment. Would you like to reschedule for another time?" },
            { role: "user", content: "I'm not sure when I'll be available. I'll call back later to reschedule." },
            { role: "agent", content: "No problem! When you're ready, just give us a call and we'll find a time that works for you." },
            { role: "user", content: "Thank you." },
            { role: "agent", content: "Thank you for letting us know. Have a good day!" },
        ] as TranscriptTurn[],
        scrubbed_transcript_with_tool_calls: [
            { role: "agent", content: "Hello, thank you for calling [CLINIC]. How can I help you today?" },
            { role: "user", content: "Hi, I need to cancel my appointment that was scheduled for tomorrow." },
            { role: "agent", content: "I'm sorry to hear that. May I ask the reason for the cancellation?" },
            { role: "user", content: "I have a work emergency and won't be able to make it." },
            { role: "tool_call_invocation", name: "cancel_appointment", arguments: "{\"appointment_id\": \"[APT-ID]\", \"reason\": \"work_emergency\"}" },
            { role: "tool_call_result", name: "cancel_appointment", content: "Appointment cancelled successfully." },
            { role: "agent", content: "I completely understand. I've cancelled your appointment. Would you like to reschedule for another time?" },
            { role: "user", content: "I'm not sure when I'll be available. I'll call back later to reschedule." },
            { role: "agent", content: "No problem! When you're ready, just give us a call and we'll find a time that works for you." },
            { role: "user", content: "Thank you." },
            { role: "agent", content: "Thank you for letting us know. Have a good day!" },
        ] as TranscriptTurn[],
        recording_url: "https://example.com/recordings/call-003.mp3",
        custom_fields: [
            { field_key: "cancellation_reason", field_name: "Cancellation Reason", field_type: "text", value: "Work emergency", is_phi: false, display_order: 1 },
        ],
    },
}

export async function listCalls(filters: CallsFilters = {}): Promise<CallsListResponse> {
    if (USE_MOCK_DATA) {
        let filteredCalls = [...MOCK_CALLS]

        if (filters.status) {
            filteredCalls = filteredCalls.filter(c => c.call_status === filters.status)
        }
        if (filters.direction) {
            filteredCalls = filteredCalls.filter(c => c.call_direction === filters.direction)
        }
        if (filters.search) {
            const search = filters.search.toLowerCase()
            filteredCalls = filteredCalls.filter(c => 
                c.summary?.toLowerCase().includes(search) ||
                c.contact?.full_name?.toLowerCase().includes(search)
            )
        }

        return {
            total: filteredCalls.length,
            limit: filters.limit ?? 25,
            offset: filters.offset ?? 0,
            items: filteredCalls.slice(filters.offset ?? 0, (filters.offset ?? 0) + (filters.limit ?? 25)),
        }
    }

    const params = new URLSearchParams();
    if (filters.limit !== undefined) params.set("limit", String(filters.limit));
    if (filters.offset !== undefined) params.set("offset", String(filters.offset));
    if (filters.status) params.set("status", filters.status);
    if (filters.tags?.length) filters.tags.forEach((t) => params.append("tags", t));
    if (filters.direction) params.set("direction", filters.direction);
    if (filters.search) params.set("search", filters.search);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);

    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<CallsListResponse>(`/institution/calls${q}`);
    return data;
}

export async function getCall(callId: string): Promise<CallDetail> {
    if (USE_MOCK_DATA) {
        const mockDetail = MOCK_CALL_DETAILS[callId]
        if (mockDetail) {
            return mockDetail
        }
        const callRecord = MOCK_CALLS.find(c => c.id === callId)
        if (callRecord) {
            const mockTranscripts: TranscriptTurn[] = [
                { role: "agent", content: "Hello, thank you for calling Smile Dental. How can I help you today?" },
                { role: "user", content: callRecord.summary || "Hello, I have a question." },
                { role: "agent", content: "I understand. Let me help you with that." },
                { role: "tool_call_invocation", name: "search_patient", arguments: "{\"query\": \"patient name\"}" },
                { role: "tool_call_result", name: "search_patient", content: "Patient found in system." },
                { role: "agent", content: "I've found your information. Is there anything else I can help you with?" },
                { role: "user", content: "No, that's all. Thank you." },
                { role: "agent", content: "Thank you for calling! Have a great day!" },
            ]
            return {
                ...callRecord,
                transcript: `Agent: Hello, thank you for calling Smile Dental. How can I help you today?\n\nPatient: ${callRecord.summary}\n\nAgent: I understand. Let me help you with that.\n\nPatient: No, that's all. Thank you.\n\nAgent: Thank you for calling! Have a great day!`,
                transcript_with_tool_calls: mockTranscripts,
                scrubbed_transcript_with_tool_calls: mockTranscripts.map(t => ({
                    ...t,
                    content: t.content?.replace(/Smile Dental/gi, "[CLINIC]")?.replace(/patient/gi, "[PATIENT]") ?? t.content
                })),
                recording_url: `https://example.com/recordings/${callId}.mp3`,
                custom_fields: [],
            }
        }
        throw new Error("Call not found")
    }

    const { data } = await api.get<CallDetail>(`/institution/calls/${callId}`);
    return data;
}

export async function resolveCallback(callId: string, _note?: string): Promise<CallRecord> {
    // const { data } = await api.patch<CallRecord>(`/institution/calls/${callId}/resolve`, {
    //     note: note ?? null,
    // });
    // return data;

    // Mock implementation
    const call = MOCK_CALLS.find(c => c.id === callId)
    if (call) {
        call.callback_resolved = true
        return call
    }
    throw new Error("Call not found")
}
