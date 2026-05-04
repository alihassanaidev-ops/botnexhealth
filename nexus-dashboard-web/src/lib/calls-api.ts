/**
 * Calls API service.
 *
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type {
    CallDetail,
    CallRecord,
    CallsListResponse,
    CustomFieldRevealResponse,
    RecordingRevealResponse,
    TranscriptRevealResponse,
} from "@/types";

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

export async function listCalls(filters: CallsFilters = {}): Promise<CallsListResponse> {
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
    const { data } = await api.get<CallDetail>(`/institution/calls/${callId}`);
    return data;
}

export async function revealTranscript(callId: string): Promise<TranscriptRevealResponse> {
    const { data } = await api.post<TranscriptRevealResponse>(
        `/institution/calls/${callId}/reveal/transcript`,
    );
    return data;
}

export async function revealRecording(callId: string): Promise<RecordingRevealResponse> {
    const { data } = await api.post<RecordingRevealResponse>(
        `/institution/calls/${callId}/reveal/recording`,
    );
    return data;
}

export async function revealCustomPhiField(
    callId: string,
    fieldKey: string,
): Promise<CustomFieldRevealResponse> {
    const { data } = await api.post<CustomFieldRevealResponse>(
        `/institution/calls/${callId}/reveal/custom-fields/${encodeURIComponent(fieldKey)}`,
    );
    return data;
}

export async function resolveCallback(callId: string, note?: string): Promise<CallRecord> {
    const { data } = await api.patch<CallRecord>(`/institution/calls/${callId}/resolve`, {
        note: note ?? null,
    });
    return data;
}
