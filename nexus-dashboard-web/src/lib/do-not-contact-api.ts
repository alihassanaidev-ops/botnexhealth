/**
 * Patient-centric do-not-contact API. INSTITUTION_ADMIN only.
 */

import api from "@/lib/api"
import type { DncPatientRecord, DncRecordType } from "@/types"

export type DncScope = "location" | "institution"

export interface CreateDoNotContactPayload {
    phone: string
    scope: DncScope
    location_id?: string | null
    contact_id?: string | null
    reason?: string | null
}

interface LegacyDncRecord {
    phone_masked: string
    scope: DncScope
}

export async function listDoNotContact(): Promise<DncPatientRecord[]> {
    const { data } = await api.get<{ records: DncPatientRecord[] }>("/institution/do-not-contact")
    return data.records
}

/** Record a staff-requested all-channel DNC. */
export async function createDoNotContact(
    payload: CreateDoNotContactPayload,
): Promise<LegacyDncRecord> {
    const { data } = await api.post<LegacyDncRecord>("/institution/do-not-contact", payload)
    return data
}

/** Release one channel tag without changing the patient's other opt-outs. */
export async function releaseDoNotContact(
    recordType: DncRecordType,
    recordId: string,
): Promise<boolean> {
    const { data } = await api.delete<{ released: boolean }>(
        `/institution/do-not-contact/entries/${recordType}/${recordId}`,
    )
    return data.released
}
