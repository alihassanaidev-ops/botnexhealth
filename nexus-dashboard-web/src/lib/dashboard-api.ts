/**
 * Dashboard API service.
 *
 * Fetches call volume metrics and callback queue from /institution/dashboard/summary.
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { DashboardSummary } from "@/types";

export async function getDashboardSummary(): Promise<DashboardSummary> {
    const { data } = await api.get<DashboardSummary>("/institution/dashboard/summary");
    return data;
}
