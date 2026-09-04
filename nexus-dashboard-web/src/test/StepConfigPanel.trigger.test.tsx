import { useState } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepConfigPanel from "@/components/workflow/StepConfigPanel"
import { TRIGGER_NODE_ID } from "@/lib/workflow/graph"
import { _resetEventCatalogCache } from "@/lib/workflow/event-catalog"
import { listEventCatalog } from "@/lib/workflow-api"
import type { WorkflowDefinition, WorkflowTrigger } from "@/types/workflow"

/**
 * This file used to cover the GoTracker status multiselect on the retired
 * `appointment_state_changed` trigger. That UI is gone: a campaign now names
 * canonical events instead of PMS statuses, so the equivalent coverage is the
 * event picker — that ticking an event rewrites `event_keys`, and that the
 * appointment-relative interval only exists for the reminder event, which is
 * the one event whose firing time the author decides.
 */

vi.mock("@/lib/workflow-api", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@/lib/workflow-api")>()),
    listEventCatalog: vi.fn(),
}))

const catalogMock = listEventCatalog as ReturnType<typeof vi.fn>

/** Catalog order, deliberately not alphabetical by label. */
const EVENTS = [
    event("appointment.cancelled", "Appointment cancelled"),
    event("appointment.completed", "Appointment completed"),
    event("appointment.reminder_due", "Appointment reminder due"),
]

function event(key: string, label: string) {
    return {
        key,
        label,
        description: `${label} description`,
        pms_support: { gotracker: "native" as const, nexhealth: "native" as const },
        context: [],
    }
}

const POST_OP_DEF: WorkflowDefinition = {
    schema_version: "1.0",
    trigger: {
        type: "event",
        event_keys: ["appointment.completed"],
        max_followup_delay_hours: 72,
        campaign_goal: "post_op_followup",
    },
    entry_node_id: "exit-1",
    nodes: [{ type: "exit", id: "exit-1", outcome: "done" }],
}

function TriggerPanelHarness({ onTriggerChange }: { onTriggerChange: (trigger: WorkflowTrigger) => void }) {
    const [def, setDef] = useState(POST_OP_DEF)
    const handleTriggerChange = (trigger: WorkflowTrigger) => {
        onTriggerChange(trigger)
        setDef((current) => ({ ...current, trigger }))
    }

    return (
        <StepConfigPanel
            open
            onOpenChange={vi.fn()}
            def={def}
            selectedId={TRIGGER_NODE_ID}
            onNodeChange={vi.fn()}
            onDefinitionChange={vi.fn()}
            onTriggerChange={handleTriggerChange}
            onDeleteNode={vi.fn()}
            onSetEntry={vi.fn()}
        />
    )
}

beforeEach(() => {
    catalogMock.mockReset()
    catalogMock.mockResolvedValue(EVENTS)
    _resetEventCatalogCache()
})

describe("StepConfigPanel event trigger", () => {
    it("offers the served events as a checkbox group and reflects the current selection", async () => {
        render(<TriggerPanelHarness onTriggerChange={vi.fn()} />)

        const group = await screen.findByRole("group", { name: "Events" })
        expect(group).toBeInTheDocument()
        expect(screen.getByRole("checkbox", { name: "Appointment completed" })).toBeChecked()
        expect(screen.getByRole("checkbox", { name: "Appointment cancelled" })).not.toBeChecked()
        expect(screen.getByRole("checkbox", { name: "Appointment reminder due" })).not.toBeChecked()

        // Nothing appointment-relative to configure: only the reminder event
        // needs an interval.
        expect(screen.queryByText("Hours relative to appointment")).not.toBeInTheDocument()
    })

    it("subscribes to several events at once, which a single-select could not express", async () => {
        const onTriggerChange = vi.fn()
        const user = userEvent.setup()
        render(<TriggerPanelHarness onTriggerChange={onTriggerChange} />)

        await user.click(await screen.findByRole("checkbox", { name: "Appointment cancelled" }))
        // Serialized in catalog order, not click order, so the same
        // subscription always writes the same definition.
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                event_keys: ["appointment.cancelled", "appointment.completed"],
            }),
        )

        await user.click(screen.getByRole("checkbox", { name: "Appointment completed" }))
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({ event_keys: ["appointment.cancelled"] }),
        )

        // Unticking everything is expressible, and the panel says why it is wrong
        // rather than silently publishing a campaign that can never start.
        await user.click(screen.getByRole("checkbox", { name: "Appointment cancelled" }))
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({ event_keys: [] }),
        )
        expect(
            screen.getByText("Pick at least one event, or this campaign can never start."),
        ).toBeInTheDocument()
    })

    it("shows the reminder interval only while the reminder event is selected", async () => {
        const onTriggerChange = vi.fn()
        const user = userEvent.setup()
        render(<TriggerPanelHarness onTriggerChange={onTriggerChange} />)

        await user.click(await screen.findByRole("checkbox", { name: "Appointment reminder due" }))
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                event_keys: ["appointment.completed", "appointment.reminder_due"],
                reminder_offset_hours: -24,
            }),
        )
        expect(screen.getByText("Hours relative to appointment")).toBeInTheDocument()

        fireEvent.change(screen.getByDisplayValue("-24"), { target: { value: "-48" } })
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({ reminder_offset_hours: -48 }),
        )

        // Dropping the reminder event drops the interval with it — the backend
        // rejects an offset on any other event.
        await user.click(screen.getByRole("checkbox", { name: "Appointment reminder due" }))
        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                event_keys: ["appointment.completed"],
                reminder_offset_hours: null,
            }),
        )
        expect(screen.queryByText("Hours relative to appointment")).not.toBeInTheDocument()
    })

    it("writes an edited post-op deadline back to the trigger definition", async () => {
        const onTriggerChange = vi.fn()
        render(<TriggerPanelHarness onTriggerChange={onTriggerChange} />)

        await screen.findByRole("group", { name: "Events" })
        fireEvent.change(screen.getByDisplayValue("72"), { target: { value: "48" } })

        expect(onTriggerChange).toHaveBeenLastCalledWith(
            expect.objectContaining({
                event_keys: ["appointment.completed"],
                max_followup_delay_hours: 48,
            }),
        )
    })
})
