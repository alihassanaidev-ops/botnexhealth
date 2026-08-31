/**
 * Quiet-hours exceptions (Item 20).
 *
 * The backend rejects an exception that would leave a clinic with no permitted
 * window at all, and returns the reason as a 422 detail. That message is
 * written for the operator, so callers should surface it verbatim rather than
 * replacing it with something generic — the whole point of validating at save
 * time is that the person can see what they did and fix it.
 */
import api from "@/lib/api"

export type QuietHoursContentClass =
    | "transactional_care"
    | "recall"
    | "sales"
    | "marketing"

export interface QuietHoursException {
    id: string
    location_id: string
    contact_id: string | null
    /** ISO date. Null means the rule applies on every date. */
    exception_date: string | null
    /** Null means the rule applies to every kind of message. */
    content_class: QuietHoursContentClass | null
    /** When true no contact is permitted and the window fields are ignored. */
    is_blocked: boolean
    /** "HH:MM:SS". Null means midnight / end of day respectively. */
    open_time: string | null
    close_time: string | null
    reason: string | null
}

export interface QuietHoursExceptionInput {
    location_id: string
    contact_id?: string | null
    exception_date?: string | null
    content_class?: QuietHoursContentClass | null
    is_blocked?: boolean
    open_time?: string | null
    close_time?: string | null
    reason?: string | null
}

const BASE = "/compliance/quiet-hours/exceptions"

export async function listQuietHoursExceptions(
    locationId: string,
): Promise<QuietHoursException[]> {
    const { data } = await api.get<QuietHoursException[]>(BASE, {
        params: { location_id: locationId },
    })
    return data
}

export async function createQuietHoursException(
    input: QuietHoursExceptionInput,
): Promise<QuietHoursException> {
    const { data } = await api.post<QuietHoursException>(BASE, input)
    return data
}

export async function updateQuietHoursException(
    exceptionId: string,
    input: Partial<QuietHoursExceptionInput>,
): Promise<QuietHoursException> {
    const { data } = await api.patch<QuietHoursException>(
        `${BASE}/${exceptionId}`,
        input,
    )
    return data
}

export async function deleteQuietHoursException(exceptionId: string): Promise<void> {
    await api.delete(`${BASE}/${exceptionId}`)
}
