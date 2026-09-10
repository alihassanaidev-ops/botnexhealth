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

/** One month of headline metrics, oldest first. */
export interface MonthlyMetricPoint {
    month: string;
    month_label: string;
    total_calls_month: number;
    appointments_booked_month: number;
    new_patients_month: number;
    booking_rate_month: number;
    avg_call_duration_seconds: number;
}

export interface DashboardMonthlyMetrics {
    points: MonthlyMetricPoint[];
    as_of: string;
}

/**
 * Month-by-month history behind the headline numbers.
 *
 * A count on its own does not say whether it is good; the same count next to the
 * five months before it does.
 */
export async function getMonthlyMetrics(
    options: { months?: number; locationSlug?: string; range?: DashboardDateRange } = {},
): Promise<DashboardMonthlyMetrics> {
    const { months = 6, locationSlug, range } = options;
    const params = new URLSearchParams({ months: String(months) });
    if (locationSlug) params.set("location_slug", locationSlug);
    // A range wins over `months`: the server switches to adaptive bucketing
    // across [start, end] when either bound is present.
    appendRange(params, range);
    const { data } = await api.get<DashboardMonthlyMetrics>(
        `/institution/dashboard/monthly-metrics?${params.toString()}`,
    );
    return data;
}
