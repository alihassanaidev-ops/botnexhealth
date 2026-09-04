/**
 * Client-side view of the served canonical event catalog.
 *
 * The catalog is fetched once per page and cached at module scope: the trigger
 * picker and the filter editor both need it, and it changes only when the
 * backend deploys.
 */

import { useEffect, useState } from "react"

import {
    listEventCatalog,
    type EventCatalogEntry,
    type EventContextField,
    type PmsSupport,
} from "@/lib/workflow-api"
import type { TriggerType } from "@/types/workflow"

/**
 * The canonical events each trigger type starts from.
 *
 * Mirrors `TRIGGER_EVENT_KEYS` in `src/app/services/automation/trigger_lookup.py`.
 * The typed triggers imply their events rather than naming them, which is what
 * lets the filter editor offer the right context fields for, say, a patient
 * reply without the author having to pick an event key.
 */
export const TRIGGER_EVENT_KEYS: Record<TriggerType, string[]> = {
    // An event trigger names its own keys; this entry stays empty so the
    // lookup below falls through to the author's selection.
    event: [],
    manual: [],
    form_submitted: [],
    internal_status: ["patient.status_changed"],
    schedule: ["patient.recall_due", "schedule.tick"],
    inbound_message: ["message.sms.inbound", "message.email.inbound"],
}

let cache: EventCatalogEntry[] | null = null
let inFlight: Promise<EventCatalogEntry[]> | null = null

/** Test hook: drop the module-scope cache between cases. */
export function _resetEventCatalogCache(): void {
    cache = null
    inFlight = null
}

export async function loadEventCatalog(): Promise<EventCatalogEntry[]> {
    if (cache) return cache
    if (!inFlight) {
        inFlight = listEventCatalog()
            .then((events) => {
                cache = events
                return events
            })
            .catch((error) => {
                // Clear the in-flight promise so a later mount can retry rather
                // than latching onto the rejected one forever.
                inFlight = null
                throw error
            })
    }
    return inFlight
}

/**
 * The event catalog, loaded once per page.
 *
 * Seeds from the cache so a second mount does not flash empty, and swallows
 * load errors — a missing catalog degrades the picker to "no events offered",
 * which is visible, rather than breaking the whole builder.
 */
export function useEventCatalog(): EventCatalogEntry[] {
    const [events, setEvents] = useState<EventCatalogEntry[]>(cache ?? [])

    useEffect(() => {
        let active = true
        loadEventCatalog()
            .then((loaded) => {
                if (active) setEvents(loaded)
            })
            .catch(() => {
                /* editor still accepts a typed field path */
            })
        return () => {
            active = false
        }
    }, [])

    return events
}

/**
 * Context fields an author may branch on for this trigger.
 *
 * For an event trigger the author's own selection decides; with nothing picked
 * yet we offer every field, so a fresh trigger does not show an empty list.
 * Fields are deduped by path across events and sorted, so the list is stable
 * whatever order the events arrive in.
 */
export function canonicalFieldsForTrigger(
    events: EventCatalogEntry[],
    triggerType: TriggerType,
    eventKeys?: string[],
): EventContextField[] {
    const selected = eventKeys?.length
        ? new Set(eventKeys)
        : triggerType === "event"
          ? null // null means "all events"
          : new Set(TRIGGER_EVENT_KEYS[triggerType] ?? [])

    const byPath = new Map<string, EventContextField>()
    for (const event of events) {
        if (selected && !selected.has(event.key)) continue
        for (const field of event.context) {
            if (!byPath.has(field.path)) byPath.set(field.path, field)
        }
    }
    return [...byPath.values()].sort((a, b) => a.path.localeCompare(b.path))
}

export function fieldSupport(
    field: EventContextField | undefined,
    pms: string | null,
): PmsSupport | undefined {
    if (!field || !pms) return undefined
    return field.pms_support[pms]
}

/** Human-readable note when the current PMS cannot supply a field outright. */
export function supportNote(
    field: EventContextField | undefined,
    pms: string | null,
): string | null {
    const support = fieldSupport(field, pms)
    if (support === "unsupported") return `Not available on ${pms}`
    if (support === "derived") return `Derived on ${pms}, not reported directly`
    return null
}

export type { EventCatalogEntry, EventContextField, PmsSupport }
