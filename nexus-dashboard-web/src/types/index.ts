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
