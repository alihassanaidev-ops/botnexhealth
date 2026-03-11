/**
 * Callbacks API service.
 *
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { CallbacksListResponse } from "@/types";

export interface CallbacksFilters {
    limit?: number;
    offset?: number;
    resolved?: boolean;
    search?: string;
    date_from?: string;
    date_to?: string;
    sort?: "oldest" | "newest";
}

export async function listCallbacks(filters: CallbacksFilters = {}): Promise<CallbacksListResponse> {
    const params = new URLSearchParams();
    if (filters.limit !== undefined) params.set("limit", String(filters.limit));
    if (filters.offset !== undefined) params.set("offset", String(filters.offset));
    if (filters.resolved !== undefined) params.set("resolved", String(filters.resolved));
    if (filters.search) params.set("search", filters.search);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    if (filters.sort) params.set("sort", filters.sort);

    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<CallbacksListResponse>(`/institution/callbacks${q}`);
    return data;
}
