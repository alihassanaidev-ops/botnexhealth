/**
 * The `+` on an unconnected port.
 *
 * Dragging from a handle already worked; the point of this affordance is that
 * it is discoverable without knowing that. So the rules it must keep are: one
 * `+` per *unconnected* port, none on a port that already goes somewhere, and
 * none at all in a read-only preview.
 *
 * Queried by attribute rather than by role and name. React Flow leaves its node
 * wrappers `visibility: hidden` until it measures them, which jsdom never does,
 * so the cards are in the DOM but accessible-name computation inside them comes
 * back empty and `getByRole(..., { name })` cannot see them.
 */
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas"
import { definitionToFlow } from "@/lib/workflow/graph"
import type { WorkflowDefinition } from "@/types/workflow"

/** sms-1 is wired to exit-1; the condition's two branches are not wired. */
const DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: { type: "manual" },
    entry_node_id: "sms-1",
    nodes: [
        { type: "send_sms", id: "sms-1", body_template: "Hi", next_node_id: "exit-1" },
        {
            type: "condition",
            id: "condition-1",
            rules: [{ field: "appointment_status", op: "eq", value: "booked" }],
            true_next_node_id: "",
            false_next_node_id: "",
        },
        { type: "exit", id: "exit-1", outcome: "sent" },
    ],
}

function renderCanvas(onAddFromPort?: (sourceId: string, handle?: string) => void) {
    const { nodes, edges } = definitionToFlow(DEF)
    return render(
        <WorkflowCanvas
            nodes={nodes}
            edges={edges}
            editable
            onAddFromPort={onAddFromPort}
        />,
    )
}

/** React Flow mounts its cards a tick after render; card text is the cheap wait. */
const ready = () => screen.findByText("Condition")

const addButtons = () =>
    Array.from(
        document.querySelectorAll<HTMLButtonElement>('button[aria-label^="Add step"]'),
    )

const addLabels = () => addButtons().map((button) => button.getAttribute("aria-label"))

describe("add-a-step port", () => {
    it("offers one + per unconnected branch, labelled by the branch", async () => {
        renderCanvas(vi.fn())
        await ready()

        expect(addLabels()).toEqual(['Add step after "Yes"', 'Add step after "No"'])
    })

    it("does not offer one on a port that already goes somewhere", async () => {
        renderCanvas(vi.fn())
        await ready()

        // sms-1 -> exit-1 is wired, so its port has no +; an unlabelled
        // "Add step" is what it would have produced.
        expect(addLabels()).not.toContain("Add step")
    })

    it("reports which node and which branch was clicked", async () => {
        const onAddFromPort = vi.fn()
        renderCanvas(onAddFromPort)
        await ready()

        const no = addButtons().find(
            (button) => button.getAttribute("aria-label") === 'Add step after "No"',
        )
        await userEvent.click(no as HTMLButtonElement)

        expect(onAddFromPort).toHaveBeenCalledWith("condition-1", "false")
    })

    it("shows nothing in a read-only preview", async () => {
        renderCanvas(undefined)
        await ready()

        expect(addButtons()).toHaveLength(0)
    })
})
