/**
 * Tenant setup API service.
 *
 * All calls go to /tenant/setup/* endpoints which read from
 * cached data where possible (saving NexHealth API costs).
 */

import api from "@/lib/api";
import type {
    CachedAppointmentType,
    CachedAvailability,
    CachedDescriptor,
    CachedOperatory,
    CachedProvider,
    LocationInfo,
    SetupOverview,
    SyncResult,
} from "@/types";

const BASE = "/tenant/setup";

function qs(locationId?: string): string {
    return locationId ? `?location_id=${locationId}` : "";
}

// ── Overview ────────────────────────────────────────────────────────────

export async function getSetupOverview(locationId?: string): Promise<SetupOverview> {
    const { data } = await api.get<SetupOverview>(`${BASE}/overview${qs(locationId)}`);
    return data;
}

// ── Locations ───────────────────────────────────────────────────────────

export async function listLocations(): Promise<LocationInfo[]> {
    const { data } = await api.get<LocationInfo[]>(`${BASE}/locations`);
    return data;
}

// ── Providers ───────────────────────────────────────────────────────────

export async function listProviders(locationId?: string): Promise<CachedProvider[]> {
    const { data } = await api.get<CachedProvider[]>(`${BASE}/providers${qs(locationId)}`);
    return data;
}

// ── Appointment Types ───────────────────────────────────────────────────

export async function listAppointmentTypes(locationId?: string): Promise<CachedAppointmentType[]> {
    const { data } = await api.get<CachedAppointmentType[]>(
        `${BASE}/appointment-types${qs(locationId)}`
    );
    return data;
}

export async function createAppointmentType(
    payload: { name: string; duration_minutes: number; descriptor_ids: string[] },
    locationId?: string
): Promise<CachedAppointmentType> {
    const { data } = await api.post<CachedAppointmentType>(
        `${BASE}/appointment-types${qs(locationId)}`,
        payload
    );
    return data;
}

export async function deleteAppointmentType(
    sourceId: string,
    locationId?: string
): Promise<void> {
    await api.delete(`${BASE}/appointment-types/${sourceId}${qs(locationId)}`);
}

// ── Operatories ─────────────────────────────────────────────────────────

export async function listOperatories(locationId?: string): Promise<CachedOperatory[]> {
    const { data } = await api.get<CachedOperatory[]>(`${BASE}/operatories${qs(locationId)}`);
    return data;
}

// ── Descriptors ─────────────────────────────────────────────────────────

export async function listDescriptors(locationId?: string): Promise<CachedDescriptor[]> {
    const { data } = await api.get<CachedDescriptor[]>(`${BASE}/descriptors${qs(locationId)}`);
    return data;
}

// ── Availabilities ──────────────────────────────────────────────────────

export async function listAvailabilities(
    locationId?: string,
    providerSourceId?: string
): Promise<CachedAvailability[]> {
    const params = new URLSearchParams();
    if (locationId) params.set("location_id", locationId);
    if (providerSourceId) params.set("provider_source_id", providerSourceId);
    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<CachedAvailability[]>(`${BASE}/availabilities${q}`);
    return data;
}

export async function updateAvailability(
    sourceId: string,
    payload: {
        appointment_type_ids?: string[];
        days?: string[];
        start_time?: string;
        end_time?: string;
        operatory_id?: string;
        active?: boolean;
    },
    locationId?: string
): Promise<CachedAvailability> {
    const { data } = await api.patch<CachedAvailability>(
        `${BASE}/availabilities/${sourceId}${qs(locationId)}`,
        payload
    );
    return data;
}

// ── Sync ────────────────────────────────────────────────────────────────

export async function triggerSync(locationId?: string): Promise<SyncResult> {
    const { data } = await api.post<SyncResult>(`${BASE}/sync${qs(locationId)}`);
    return data;
}

// ── Calls (GHL) ─────────────────────────────────────────────────────────

interface CallFilters {
    status?: string;
    search?: string;
    page?: number;
    page_size?: number;
}

export async function listCalls(filters?: CallFilters): Promise<import("@/types").CallsResponse> {
    const params = new URLSearchParams();
    if (filters?.status) params.set("status", filters.status);
    if (filters?.search) params.set("search", filters.search);
    if (filters?.page) params.set("page", String(filters.page));
    if (filters?.page_size) params.set("page_size", String(filters.page_size));
    const q = params.toString() ? `?${params.toString()}` : "";
    const { data } = await api.get<import("@/types").CallsResponse>(`/tenant/calls${q}`);
    return data;
}

