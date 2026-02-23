/**
 * Calls API service.
 *
 * Fetches paginated call records from /tenant/calls.
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { CallsListResponse } from "@/types";

export interface CallsFilters {
    limit?: number;
    offset?: number;
    status?: string;
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
    if (filters.direction) params.set("direction", filters.direction);
    if (filters.search) params.set("search", filters.search);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);

    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<CallsListResponse>(`/tenant/calls${q}`);
    return data;
}
