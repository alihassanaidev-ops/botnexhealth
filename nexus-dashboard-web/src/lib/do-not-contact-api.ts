/**
 * Do-Not-Contact API — staff-initiated opt-outs recorded off-channel
 * (in person, by phone to a human, or by email). INSTITUTION_ADMIN only.
 *
 * The compliance gate already *honors* a DoNotContact (blocks every channel for
 * its scope tier); this is the privileged entry point to record and release one.
 * Backend: POST/DELETE/GET /institution/do-not-contact (src/app/api/routes/do_not_contact.py).
 */

import api from "@/lib/api"
import type { DncRecord } from "@/types"

export type DncScope = "location" | "institution"

export interface CreateDoNotContactPayload {
    phone: string
    scope: DncScope
    location_id?: string | null
    contact_id?: string | null
    reason?: string | null
}

export async function listDoNotContact(): Promise<DncRecord[]> {
    const { data } = await api.get<{ records: DncRecord[] }>("/institution/do-not-contact")
    return data.records
}

export async function createDoNotContact(
    payload: CreateDoNotContactPayload,
): Promise<DncRecord> {
    const { data } = await api.post<DncRecord>("/institution/do-not-contact", payload)
    return data
}

/** Release an active do-not-contact for a phone. Idempotent. */
export async function releaseDoNotContact(phone: string): Promise<boolean> {
    const { data } = await api.delete<{ released: boolean }>("/institution/do-not-contact", {
        data: { phone },
    })
    return data.released
}
