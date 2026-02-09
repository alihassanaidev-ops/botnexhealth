export interface User {
    id: string;
    email: string;
    role?: string;
    is_active?: boolean;
    tenant_id?: string;
}

export interface Tenant {
    id: string;
    name: string;
    slug: string;
    is_active: boolean;
    created_at?: string;
    updated_at?: string;
}

export interface TenantUser {
    id: string;
    email: string;
    role: string;
    is_active: boolean;
}

export interface TenantDetail {
    id: string;
    name: string;
    slug: string;
    is_active: boolean;

    nexhealth_subdomain: string | null;
    nexhealth_location_id: string | null;
    ghl_location_id: string | null;
    ghl_custom_fields: Record<string, string> | null;
    retell_agent_id: string | null;
    sikka_office_id: string | null;

    has_nexhealth_key: boolean;
    has_ghl_key: boolean;
    has_retell_secret: boolean;
    has_sikka_credentials: boolean;

    user: TenantUser | null;
}

export interface Location {
    id: string;
    tenant_id: string;
    name: string;
    slug: string;
    is_active: boolean;

    nexhealth_subdomain: string | null;
    nexhealth_location_id: string | null;
    retell_agent_id: string | null;
    has_retell_secret: boolean;

    address: string | null;
    city: string | null;
    state: string | null;
    phone: string | null;
    timezone: string | null;
}

export interface SyncResult {
    location: string;
    success: boolean;
    providers_synced: number;
    appointment_types_synced: number;
    errors: string[];
}
