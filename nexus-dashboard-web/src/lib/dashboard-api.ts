/**
 * Dashboard API service.
 *
 * Fetches call volume metrics and callback queue from /institution/dashboard/summary.
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { DashboardSummary } from "@/types";
import type { AggregateDashboardResponse } from "./institution-portal-api";

export async function getDashboardSummary(locationSlug?: string): Promise<DashboardSummary> {
    const params = new URLSearchParams();
    if (locationSlug) params.set("location_slug", locationSlug);
    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<DashboardSummary>(`/institution/dashboard/summary${q}`);
    return data;
}

export async function getAggregateDashboard(): Promise<AggregateDashboardResponse> {
    const { data } = await api.get<AggregateDashboardResponse>("/institution/dashboard/aggregate");
    return data;
}
