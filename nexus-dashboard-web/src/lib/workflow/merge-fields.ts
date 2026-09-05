/**
 * Merge-field catalog + sample data.
 *
 * Sourced from `GET /automation/workflows/merge-fields`, and from nothing else.
 * There was a hand-maintained static mirror of the backend catalog standing in
 * during initial render and offline, which had two failure modes worth avoiding:
 * it drifted from the real catalog, so the builder could offer a field the
 * backend does not have, and it hid the outage that made it appear at all.
 *
 * Until the catalog loads there is no catalog. Callers ask `catalogLoaded()`
 * before asserting anything about a token — claiming a token is unknown on the
 * strength of an empty list would flag every message in the workflow.
 */
import { useEffect, useState } from "react"
import type { MergeField, TriggerType } from "@/types/workflow"
import { listMergeFields } from "@/lib/workflow-api"

type MergeChannel = "sms" | "email" | "voice"

let catalog: MergeField[] = []
let loaded = false
const scopedCatalog = new Map<string, MergeField[]>()
const fetchPromises = new Map<string, Promise<MergeField[]>>()

/** Whether the authoritative catalog has been fetched at least once. */
export function catalogLoaded(): boolean {
    return loaded
}

/** The fetched merge-field catalog; empty until it loads. */
export function getMergeFields(opts?: {
    triggerType?: TriggerType
    channel?: MergeChannel
}): MergeField[] {
    const key = cacheKey(opts)
    return scopedCatalog.get(key) ?? filterFields(catalog, opts)
}

/**
 * Fetch the backend catalog once and cache it. Idempotent; a failed fetch is
 * not cached, so a later call retries rather than settling on an empty list.
 */
export async function loadMergeFields(opts?: {
    triggerType?: TriggerType
    channel?: MergeChannel
}): Promise<MergeField[]> {
    const key = cacheKey(opts)
    let fetchPromise = fetchPromises.get(key)
    if (!fetchPromise) {
        fetchPromise = listMergeFields(opts)
            .then((fields) => {
                const fetched = fields.map((f) => ({
                    name: f.name,
                    token: f.token,
                    label: f.label,
                    sample: f.sample,
                    description: f.description,
                    group: f.group,
                    availability: f.availability,
                    requires: f.requires,
                    phi_level: f.phi_level,
                    channels: f.channels,
                    trigger_types: f.trigger_types,
                }))
                if (key === "all:all") catalog = fetched
                loaded = true
                scopedCatalog.set(key, fetched)
                return fetched
            })
            .catch((err) => {
                fetchPromises.delete(key) // allow a later retry
                throw err
            })
        fetchPromises.set(key, fetchPromise)
    }
    return fetchPromise
}

/** Test-only: reset the module cache. */
export function _resetMergeFieldsCache(): void {
    catalog = []
    loaded = false
    scopedCatalog.clear()
    fetchPromises.clear()
}

export type MergeFieldsStatus = "loading" | "ready" | "error"

/**
 * React hook: the catalog, fetched and cached once.
 *
 * The status is part of the return value rather than swallowed, because the
 * only honest thing to render on a failed fetch is a failure — a stale or
 * invented list of insertable fields is how someone picks a token the backend
 * will not resolve.
 */
export function useMergeFields(opts?: {
    triggerType?: TriggerType
    channel?: MergeChannel
}): { fields: MergeField[]; status: MergeFieldsStatus } {
    const triggerType = opts?.triggerType
    const channel = opts?.channel
    const [fields, setFields] = useState<MergeField[]>(
        getMergeFields({ triggerType, channel }),
    )
    const [status, setStatus] = useState<MergeFieldsStatus>(
        catalogLoaded() ? "ready" : "loading",
    )
    useEffect(() => {
        let active = true
        setStatus(catalogLoaded() ? "ready" : "loading")
        loadMergeFields({ triggerType, channel })
            .then((f) => {
                if (!active) return
                setFields(f)
                setStatus("ready")
            })
            .catch(() => {
                if (!active) return
                setFields([])
                setStatus("error")
            })
        return () => {
            active = false
        }
    }, [triggerType, channel])
    return { fields, status }
}

/** Map of token -> sample value, for preview/simulation. */
export function sampleMergeData(): Record<string, string> {
    const out: Record<string, string> = {}
    for (const f of catalog) out[f.token] = f.sample
    return out
}

const TOKEN_RE = /\{\{\s*[a-zA-Z0-9_]+\s*\}\}/g

/** Normalize a raw token (trim inner whitespace) to the catalog form. */
export function normalizeToken(raw: string): string {
    const inner = raw.replace(/[{}]/g, "").trim()
    return `{{${inner}}}`
}

/** All merge tokens referenced in a template string (normalized). */
export function extractTokens(template: string): string[] {
    const matches = template.match(TOKEN_RE) ?? []
    return matches.map(normalizeToken)
}

/**
 * Tokens in the template that are not in the fetched catalog.
 *
 * Empty while the catalog is unloaded: with nothing to compare against every
 * token would read as unknown. Publish-time validation is server-side and
 * fail-closed, so nothing is let through by staying quiet here.
 */
export function unknownTokens(template: string): string[] {
    if (!loaded) return []
    const known = new Set(catalog.map((f) => f.token))
    return Array.from(new Set(extractTokens(template))).filter((t) => !known.has(t))
}

/** As `unknownTokens`: silent until the catalog is loaded. */
export function unavailableTokens(
    template: string,
    opts: { triggerType: TriggerType; channel: MergeChannel },
): string[] {
    if (!loaded) return []
    const byToken = new Map(catalog.map((f) => [f.token, f]))
    return Array.from(new Set(extractTokens(template))).filter((token) => {
        const field = byToken.get(token)
        if (!field) return false
        return (
            (field.trigger_types?.length && !field.trigger_types.includes(opts.triggerType))
            || (field.channels?.length && !field.channels.includes(opts.channel))
        )
    })
}

function cacheKey(opts?: { triggerType?: TriggerType; channel?: MergeChannel }): string {
    return `${opts?.triggerType ?? "all"}:${opts?.channel ?? "all"}`
}

function filterFields(
    fields: MergeField[],
    opts?: { triggerType?: TriggerType; channel?: MergeChannel },
): MergeField[] {
    return fields.filter((f) => {
        const triggerOk = !opts?.triggerType || !f.trigger_types || f.trigger_types.includes(opts.triggerType)
        const channelOk = !opts?.channel || !f.channels || f.channels.includes(opts.channel)
        return triggerOk && channelOk
    })
}
