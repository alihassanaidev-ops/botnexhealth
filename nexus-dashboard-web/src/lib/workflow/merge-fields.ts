/**
 * Merge-field catalog + sample data.
 *
 * NOTE (limitation): there is no backend merge-field catalog endpoint yet
 * (findings.md §3). This client-side catalog is the source of truth for the
 * builder's preview/insert affordances. When a backend catalog lands (owned by
 * Plan 01/06), `MERGE_FIELDS` can be replaced by a fetched list without changing
 * any consumer.
 */
import type { MergeField } from "@/types/workflow"

export const MERGE_FIELDS: MergeField[] = [
    { token: "{{patient_first_name}}", label: "Patient first name", sample: "Jordan" },
    { token: "{{patient_last_name}}", label: "Patient last name", sample: "Rivera" },
    { token: "{{clinic_name}}", label: "Clinic name", sample: "Bright Smiles Dental" },
    { token: "{{provider_name}}", label: "Provider name", sample: "Dr. Lee" },
    { token: "{{appointment_date}}", label: "Appointment date", sample: "Tue, Jul 8" },
    { token: "{{appointment_time}}", label: "Appointment time", sample: "2:30 PM" },
]

/** Map of token -> sample value, for preview/simulation. */
export function sampleMergeData(): Record<string, string> {
    const out: Record<string, string> = {}
    for (const f of MERGE_FIELDS) out[f.token] = f.sample
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

/** Tokens in the template that are not in the known catalog. */
export function unknownTokens(template: string): string[] {
    const known = new Set(MERGE_FIELDS.map((f) => f.token))
    return Array.from(new Set(extractTokens(template))).filter((t) => !known.has(t))
}
