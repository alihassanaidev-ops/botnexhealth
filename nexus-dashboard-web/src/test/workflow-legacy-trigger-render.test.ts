/**
 * A campaign published before the trigger rearchitecture must still open.
 *
 * The stored definition keeps its original trigger type — the backend converts
 * on read but never rewrites the JSON — so the builder receives types that are
 * no longer in `TRIGGER_META`. Two components read `.icon` straight off that
 * lookup, which turned three live staging campaigns into a blank page.
 */
import { describe, it, expect } from "vitest"
import { triggerMetaFor, TRIGGER_META } from "@/lib/workflow/catalog"
import type { TriggerType } from "@/types/workflow"

// Definitions published before the trigger rearchitecture still store these,
// because the backend converts them on read and never rewrites the stored JSON.
// `WorkflowNode` and `WorkflowPalette` read `.icon` straight off the lookup, so
// an undefined here is a blank page, not a missing label.
const RETIRED = [
    "appointment_offset",
    "appointment_state_changed",
    "recall_scan",
    "bulk_import",
    "enquiry_received",
    "callback_requested",
    "patient_status_changed",
    "sms_reply",
    "email_reply",
]

describe("trigger metadata is total", () => {
    it.each(RETIRED)("%s still renders", (type) => {
        const meta = triggerMetaFor(type)
        expect(meta.icon).toBeTruthy()
        expect(meta.label).toBeTruthy()
        expect(meta.label).not.toBe(type)
    })

    it.each(Object.keys(TRIGGER_META))("%s keeps its own metadata", (type) => {
        expect(triggerMetaFor(type)).toBe(TRIGGER_META[type as TriggerType])
    })

    it("an unrecognised type degrades instead of throwing", () => {
        expect(triggerMetaFor("something_new").icon).toBeTruthy()
    })
})
