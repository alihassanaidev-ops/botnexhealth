/**
 * Dashboard API service.
 *
 * Fetches call volume metrics and callback queue from /tenant/dashboard/summary.
 * JWT is added automatically by the Axios interceptor in api.ts.
 */

import api from "@/lib/api";
import type { DashboardSummary } from "@/types";

export async function getDashboardSummary(): Promise<DashboardSummary> {
    const { data } = await api.get<DashboardSummary>("/tenant/dashboard/summary");
    return data;
}
