import { describe, it, expect } from "vitest"

import {
    NODE_META,
    TRIGGER_META,
    nodeTypeLabel,
    triggerTypeLabel,
    type NodeType,
    type TriggerType,
} from "@/lib/workflow/catalog"

// These two helpers are handed values that came off the API. The TypeScript
// type is an assertion about the payload, not a guarantee from it: a stored
// definition can name a node or trigger this build has never heard of, or a
// row can be missing the field entirely. They used to index straight into the
// catalog, so `.label` of undefined threw during render and the error boundary
// replaced the whole page — which is what took out the campaign templates page.
describe("workflow catalog labels", () => {
    it("labels every type the catalog knows", () => {
        for (const type of Object.keys(NODE_META) as NodeType[]) {
            expect(nodeTypeLabel(type)).toBe(NODE_META[type].label)
        }
        for (const type of Object.keys(TRIGGER_META) as TriggerType[]) {
            expect(triggerTypeLabel(type)).toBe(TRIGGER_META[type].label)
        }
    })

    it("falls back to a readable label instead of throwing on an unknown type", () => {
        expect(nodeTypeLabel("send_carrier_pigeon" as NodeType)).toBe("Send carrier pigeon")
        expect(triggerTypeLabel("moon_phase_changed" as TriggerType)).toBe("Moon phase changed")
    })

    it("survives a missing type, which is how the payload arrives when the field is null", () => {
        for (const missing of [undefined, null, ""]) {
            expect(nodeTypeLabel(missing as unknown as NodeType)).toBe("Unknown")
            expect(triggerTypeLabel(missing as unknown as TriggerType)).toBe("Unknown")
        }
    })
})
