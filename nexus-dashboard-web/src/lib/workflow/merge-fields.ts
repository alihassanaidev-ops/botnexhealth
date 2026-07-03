/**
 * Merge-field catalog + sample data.
 *
 * The catalog is sourced from the backend `GET /automation/workflows/merge-fields`
 * endpoint (single source of truth = the renderer's `STATIC_MERGE_FIELDS`), fetched
 * once and cached module-side. A small static fallback keeps preview/insert/validation
 * working during the initial render and offline; it mirrors the backend tokens so the
 * builder never advertises a token the engine can't resolve.
 */
import { useEffect, useState } from "react"
import type { MergeField } from "@/types/workflow"
import { listMergeFields } from "@/lib/workflow-api"

/**
 * Static fallback — mirrors the backend `STATIC_MERGE_FIELDS` tokens
 * (patient_first_name, patient_last_name, patient_full_name, clinic_name). No
 * provider/appointment tokens: the engine cannot resolve them today.
 */
export const FALLBACK_MERGE_FIELDS: MergeField[] = [
    { token: "{{patient_first_name}}", label: "Patient first name", sample: "Jordan" },
    { token: "{{patient_last_name}}", label: "Patient last name", sample: "Rivera" },
    { token: "{{patient_full_name}}", label: "Patient full name", sample: "Jordan Rivera" },
    { token: "{{clinic_name}}", label: "Clinic name", sample: "Riverside Dental" },
]

let catalog: MergeField[] = FALLBACK_MERGE_FIELDS
let fetchPromise: Promise<MergeField[]> | null = null

/** The current merge-field catalog (fallback until the backend catalog loads). */
export function getMergeFields(): MergeField[] {
    return catalog
}

/**
 * Fetch the backend catalog once and cache it. Idempotent; on failure the
 * cached fallback stays in place and a retry is permitted.
 */
export async function loadMergeFields(): Promise<MergeField[]> {
    if (!fetchPromise) {
        fetchPromise = listMergeFields()
            .then((fields) => {
                catalog = fields.map((f) => ({
                    token: f.token,
                    label: f.label,
                    sample: f.sample,
                }))
                return catalog
            })
            .catch((err) => {
                fetchPromise = null // allow a later retry
                throw err
            })
    }
    return fetchPromise
}

/** Test-only: reset the module cache. */
export function _resetMergeFieldsCache(): void {
    catalog = FALLBACK_MERGE_FIELDS
    fetchPromise = null
}

/** React hook: returns the catalog, fetching + caching from the backend once. */
export function useMergeFields(): MergeField[] {
    const [fields, setFields] = useState<MergeField[]>(catalog)
    useEffect(() => {
        let active = true
        loadMergeFields()
            .then((f) => {
                if (active) setFields(f)
            })
            .catch(() => {
                /* keep fallback; callers toast if they need to */
            })
        return () => {
            active = false
        }
    }, [])
    return fields
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

/** Tokens in the template that are not in the known (fetched) catalog. */
export function unknownTokens(template: string): string[] {
    const known = new Set(catalog.map((f) => f.token))
    return Array.from(new Set(extractTokens(template))).filter((t) => !known.has(t))
}
