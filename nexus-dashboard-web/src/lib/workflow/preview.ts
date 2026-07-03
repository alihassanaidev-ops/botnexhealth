/**
 * Client-side message preview: substitute merge tokens with sample data.
 * There is no backend preview endpoint (findings.md §3); this renders locally.
 */
import { normalizeToken, sampleMergeData } from "./merge-fields"

const TOKEN_RE = /\{\{\s*[a-zA-Z0-9_]+\s*\}\}/g

/**
 * Render a template string, replacing `{{token}}` with `data[token]`.
 * Unknown tokens are rendered as a readable placeholder like `[token]` so the
 * preview never shows raw braces and it is obvious which field is missing.
 */
export function renderTemplate(
    template: string,
    data: Record<string, string> = sampleMergeData(),
): string {
    return template.replace(TOKEN_RE, (match) => {
        const token = normalizeToken(match)
        if (token in data) return data[token]
        const inner = token.replace(/[{}]/g, "")
        return `[${inner}]`
    })
}

/** Approximate SMS segment count (GSM-7: 160 single / 153 concatenated). */
export function smsSegments(rendered: string): number {
    const len = rendered.length
    if (len === 0) return 0
    if (len <= 160) return 1
    return Math.ceil(len / 153)
}
