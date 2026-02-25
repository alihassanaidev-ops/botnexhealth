import api from "@/lib/api"
import type { TenantDetail } from "@/types"

export interface RetellAgent {
    agent_id: string
    agent_name: string
    language: string
    voice_id: string
}

export async function listTenantsDetailed(): Promise<TenantDetail[]> {
    const { data } = await api.get<TenantDetail[]>("/admin/tenants")
    return data
}

export async function listRetellAgents(): Promise<RetellAgent[]> {
    const { data } = await api.get<RetellAgent[]>("/admin/tenants/retell/agents")
    return data
}
