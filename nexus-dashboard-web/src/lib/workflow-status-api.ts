/**
 * Workflow Status API — tenant-defined call workflow states.
 *
 * Definition CRUD lives under /institution/statuses (INSTITUTION_ADMIN or
 * LOCATION_ADMIN). Assigning a status to a call is a call-scoped PATCH that any
 * active user with access to the call may perform.
 */

import api from "@/lib/api";
import type { CallRecord, WorkflowStatus } from "@/types";

export interface CreateWorkflowStatusPayload {
    name: string;
    color: string;
    display_order?: number;
}

export interface UpdateWorkflowStatusPayload {
    name?: string;
    color?: string;
    display_order?: number;
    is_active?: boolean;
}

export async function listWorkflowStatuses(includeInactive = false): Promise<WorkflowStatus[]> {
    const q = includeInactive ? "?include_inactive=true" : "";
    const { data } = await api.get<WorkflowStatus[]>(`/institution/statuses${q}`);
    return data;
}

export async function createWorkflowStatus(
    payload: CreateWorkflowStatusPayload,
): Promise<WorkflowStatus> {
    const { data } = await api.post<WorkflowStatus>("/institution/statuses", payload);
    return data;
}

export async function updateWorkflowStatus(
    id: string,
    payload: UpdateWorkflowStatusPayload,
): Promise<WorkflowStatus> {
    const { data } = await api.patch<WorkflowStatus>(`/institution/statuses/${id}`, payload);
    return data;
}

export async function deleteWorkflowStatus(id: string, hard = false): Promise<void> {
    await api.delete(`/institution/statuses/${id}${hard ? "?hard=true" : ""}`);
}

/** Assign (status_id) or clear (null) the workflow status on a call. */
export async function assignCallStatus(
    callId: string,
    statusId: string | null,
): Promise<CallRecord> {
    const { data } = await api.patch<CallRecord>(`/institution/calls/${callId}/status`, {
        status_id: statusId,
    });
    return data;
}
