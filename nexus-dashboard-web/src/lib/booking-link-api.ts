import axios from "axios"

/**
 * The public booking-link API.
 *
 * Deliberately a bare axios instance, not the shared `api` client: that one
 * attaches the dashboard's auth token and redirects to /login on a 401. A
 * patient opening this from a text message has no session, and being bounced to
 * a login screen is exactly the failure this whole flow exists to avoid.
 */
const client = axios.create({
    baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000/api",
})

export type SlotOption = {
    start: string
    end: string
    provider_id: string
    provider_name: string
}

export type SlotsResponse = {
    slots: SlotOption[]
    clinic_name: string
    timezone: string | null
    already_booked: boolean
}

export type BookOutcome = {
    status: "booked" | "pending" | "already_booked"
    start?: string
}

/** A 409 carries the refreshed list, so recovery costs no extra round trip. */
export type SlotTakenError = {
    error: "slot_taken"
    slots: SlotOption[]
}

export type LinkAction = "book" | "reschedule"

export async function fetchSlots(action: LinkAction, token: string): Promise<SlotsResponse> {
    const { data } = await client.get<SlotsResponse>(
        `/campaigns/link/${action}/slots`,
        { params: { token } },
    )
    return data
}

export async function bookSlot(
    action: LinkAction,
    token: string,
    slotStart: string,
): Promise<BookOutcome> {
    // Only the chosen start time is sent. The server re-checks availability and
    // books the slot it finds, so nothing here can widen what gets booked.
    const { data } = await client.post<BookOutcome>(
        `/campaigns/link/${action}/slots`,
        { slot_start: slotStart },
        { params: { token } },
    )
    return data
}
