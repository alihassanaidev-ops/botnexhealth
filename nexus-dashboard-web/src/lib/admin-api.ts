import api from "@/lib/api"
import type { TenantDetail } from "@/types"

export async function listTenantsDetailed(): Promise<TenantDetail[]> {
    const { data } = await api.get<TenantDetail[]>("/admin/tenants")
    return data
}

export async function verifyRetellAgent(agentId: string): Promise<any> {
    const { data } = await api.get(`/admin/tenants/retell/agents/${agentId}`)
    return data
}
