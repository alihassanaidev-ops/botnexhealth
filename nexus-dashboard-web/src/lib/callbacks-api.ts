/**
 * Callbacks API service.
 *
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { CallbacksListResponse } from "@/types";

export interface CallbacksFilters {
    locationId?: string;
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
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    if (filters.sort) params.set("sort", filters.sort);
    if (filters.locationId) params.set("location_id", filters.locationId);

    // A contact name is PHI (HIPAA H-1): it goes in the request body, not the URL.
    const { data } = await api.post<CallbacksListResponse>(
        "/institution/callbacks",
        { search: filters.search ?? null },
        { params },
    );
    return data;
}
