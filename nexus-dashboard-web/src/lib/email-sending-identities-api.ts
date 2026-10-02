import api from "@/lib/api"

const BASE = "/institution/email-sending-identities"

export type EmailIdentityStatus =
    | "pending_dns"
    | "verifying"
    | "verified"
    | "failed"
    | "revoked"

export interface EmailDnsRecord {
    name: string
    type: string
    value: string
    purpose: "sending" | "mail_from" | "receiving"
}

export interface EmailSenderAddress {
    id: string
    email_identity_id: string
    institution_id: string
    location_id: string | null
    local_part: string
    from_address: string
    from_name: string | null
    external_reply_to: string | null
    is_active: boolean
    is_default: boolean
}

export interface EmailSendingIdentity {
    id: string
    institution_id: string
    location_id: string | null
    provider: string
    domain: string
    from_address: string
    from_name: string | null
    reply_to_address: string | null
    status: EmailIdentityStatus
    /** Explicit operator rollout state; verification alone does not enable sending. */
    is_active: boolean
    /** Only a verified domain is actually used for sending. */
    is_sendable: boolean
    can_activate: boolean
    activation_blocker: string | null
    dns_records: EmailDnsRecord[]
    /** False means the records below still have to be published by hand. */
    dns_self_published: boolean
    verified_at: string | null
    last_checked_at: string | null
    failure_reason: string | null
    inbound_domain: string | null
    inbound_enabled: boolean
    inbound_dns_records: EmailDnsRecord[]
    addresses: EmailSenderAddress[]
}

/**
 * `institutionId` is for platform administrators, who have no institution of
 * their own and must name the one they are administering. A clinic admin omits
 * it — the API pins them to their own institution and refuses any other.
 */
function scoped(institutionId?: string) {
    return institutionId ? { params: { institution_id: institutionId } } : undefined
}

export async function listEmailSendingIdentities(
    institutionId?: string,
): Promise<EmailSendingIdentity[]> {
    const { data } = await api.get<{ identities: EmailSendingIdentity[] }>(
        BASE,
        scoped(institutionId),
    )
    return data.identities
}

export async function updateEmailSendingIdentity(
    id: string,
    body: { from_name?: string | null; reply_to_address?: string | null },
    institutionId?: string,
): Promise<EmailSendingIdentity> {
    const { data } = await api.put<EmailSendingIdentity>(
        `${BASE}/${id}`,
        body,
        scoped(institutionId),
    )
    return data
}

/** Re-check verification now rather than waiting for the hourly sweep. */
export async function verifyEmailSendingIdentity(
    id: string,
    institutionId?: string,
): Promise<EmailSendingIdentity> {
    const { data } = await api.post<EmailSendingIdentity>(
        `${BASE}/${id}/verify`,
        undefined,
        scoped(institutionId),
    )
    return data
}

/** Super admin only. */
export async function provisionEmailSendingIdentity(body: {
    institution_id: string
    location_id?: string | null
    from_name?: string | null
    reply_to_address?: string | null
    local_part?: string
    domain?: string | null
    inbound_domain?: string | null
}): Promise<EmailSendingIdentity> {
    const { data } = await api.post<EmailSendingIdentity>(`${BASE}/provision`, body)
    return data
}

/** Super admin only. */
export async function deleteEmailSendingIdentity(id: string): Promise<void> {
    await api.delete(`${BASE}/${id}`)
}

/** Super admin only. */
export async function activateEmailSendingIdentity(
    id: string,
): Promise<EmailSendingIdentity> {
    const { data } = await api.post<EmailSendingIdentity>(`${BASE}/${id}/activate`)
    return data
}

/** Super admin only. */
export async function deactivateEmailSendingIdentity(
    id: string,
): Promise<EmailSendingIdentity> {
    const { data } = await api.post<EmailSendingIdentity>(`${BASE}/${id}/deactivate`)
    return data
}

export async function activateInboundDomain(id: string): Promise<EmailSendingIdentity> {
    const { data } = await api.post<EmailSendingIdentity>(`${BASE}/${id}/activate-inbound`)
    return data
}

export async function deactivateInboundDomain(id: string): Promise<EmailSendingIdentity> {
    const { data } = await api.post<EmailSendingIdentity>(`${BASE}/${id}/deactivate-inbound`)
    return data
}

export async function createEmailSenderAddress(
    identityId: string,
    body: {
        location_id?: string | null
        local_part: string
        from_name?: string | null
        external_reply_to?: string | null
        make_default?: boolean | null
    },
): Promise<EmailSenderAddress> {
    const { data } = await api.post<EmailSenderAddress>(`${BASE}/${identityId}/addresses`, body)
    return data
}

export async function updateEmailSenderAddress(
    addressId: string,
    body: { from_name?: string | null; external_reply_to?: string | null; is_active?: boolean },
): Promise<EmailSenderAddress> {
    const { data } = await api.put<EmailSenderAddress>(`${BASE}/addresses/${addressId}`, body)
    return data
}

export async function makeEmailSenderAddressDefault(addressId: string): Promise<EmailSenderAddress> {
    const { data } = await api.post<EmailSenderAddress>(`${BASE}/addresses/${addressId}/default`)
    return data
}

export async function deleteEmailSenderAddress(addressId: string): Promise<void> {
    await api.delete(`${BASE}/addresses/${addressId}`)
}
