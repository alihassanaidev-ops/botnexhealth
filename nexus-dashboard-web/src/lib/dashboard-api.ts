/**
 * Dashboard API service.
 *
 * Fetches call volume metrics and callback queue from /institution/dashboard/summary.
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { DashboardSummary } from "@/types";
import type { AggregateDashboardResponse } from "./institution-portal-api";

/** Inclusive date range as ISO `yyyy-MM-dd` strings. */
export interface DashboardDateRange {
    startDate?: string;
    endDate?: string;
}

function appendRange(params: URLSearchParams, range?: DashboardDateRange): void {
    if (range?.startDate) params.set("start_date", range.startDate);
    if (range?.endDate) params.set("end_date", range.endDate);
}

export async function getDashboardSummary(
    locationSlug?: string,
    range?: DashboardDateRange,
): Promise<DashboardSummary> {
    const params = new URLSearchParams();
    if (locationSlug) params.set("location_slug", locationSlug);
    appendRange(params, range);
    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<DashboardSummary>(`/institution/dashboard/summary${q}`);
    return data;
}

export async function getAggregateDashboard(
    range?: DashboardDateRange,
): Promise<AggregateDashboardResponse> {
    const params = new URLSearchParams();
    appendRange(params, range);
    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<AggregateDashboardResponse>(`/institution/dashboard/aggregate${q}`);
    return data;
}
