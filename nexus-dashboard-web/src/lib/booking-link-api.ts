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

export type AppointmentTypeOption = {
    id: string
    name: string
    duration_minutes: number | null
}

export async function fetchAppointmentTypes(
    action: LinkAction,
    token: string,
): Promise<AppointmentTypeOption[]> {
    const { data } = await client.get<{ appointment_types: AppointmentTypeOption[] }>(
        `/campaigns/link/${action}/appointment-types`,
        { params: { token } },
    )
    return data.appointment_types
}

export async function fetchSlots(
    action: LinkAction,
    token: string,
    opts: { appointmentTypeId?: string; startDate?: string; days?: number } = {},
): Promise<SlotsResponse> {
    const { data } = await client.get<SlotsResponse>(
        `/campaigns/link/${action}/slots`,
        {
            params: {
                token,
                appointment_type_id: opts.appointmentTypeId || undefined,
                start_date: opts.startDate || undefined,
                days: opts.days,
            },
        },
    )
    return data
}

export async function bookSlot(
    action: LinkAction,
    token: string,
    slotStart: string,
    appointmentTypeId?: string,
): Promise<BookOutcome> {
    // Only the chosen start time is sent. The server re-checks availability and
    // books the slot it finds, so nothing here can widen what gets booked.
    const { data } = await client.post<BookOutcome>(
        `/campaigns/link/${action}/slots`,
        { slot_start: slotStart, appointment_type_id: appointmentTypeId || null },
        { params: { token } },
    )
    return data
}


export type CancellationDetails = {
    already_cancelled: boolean
    clinic_name: string
    appointment: {
        start: string
        provider_name: string
        reason: string
    } | null
}

export async function fetchCancellation(token: string): Promise<CancellationDetails> {
    // A read. The cancellation itself is the POST below, because link previews
    // follow GETs and would otherwise cancel appointments nobody tapped.
    const { data } = await client.get<CancellationDetails>(
        "/campaigns/link/cancel/appointment",
        { params: { token } },
    )
    return data
}

export async function cancelAppointment(
    token: string,
): Promise<{ status: "cancelled" | "already_cancelled" }> {
    const { data } = await client.post<{ status: "cancelled" | "already_cancelled" }>(
        "/campaigns/link/cancel/appointment",
        {},
        { params: { token } },
    )
    return data
}

export type RegistrationDetails = {
    clinic_name: string
    first_name: string
    last_name: string
    email: string
    phone: string
    already_registered: boolean
}

/** The closed set the practice software accepts. */
export const GENDERS = ["Female", "Male", "Other"] as const
export type Gender = (typeof GENDERS)[number]

export type RegistrationRequest = {
    date_of_birth: string
    gender: Gender
    first_name?: string
    last_name?: string
    email?: string
    phone?: string
}

export type RegisterOutcome = {
    status: "registered" | "already_registered"
}

export async function fetchRegistrationDetails(
    token: string,
): Promise<RegistrationDetails> {
    const { data } = await client.get<RegistrationDetails>(
        "/campaigns/link/register/details",
        { params: { token } },
    )
    return data
}

export async function registerPatient(
    token: string,
    body: RegistrationRequest,
): Promise<RegisterOutcome> {
    const { data } = await client.post<RegisterOutcome>(
        "/campaigns/link/register",
        body,
        { params: { token } },
    )
    return data
}
