import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import WorkflowPalette from "@/components/workflow/WorkflowPalette"


describe("WorkflowPalette capability contract", () => {
    it("only offers node types the deployed engine marks authorable and executable", () => {
        render(
            <WorkflowPalette
                trigger={{ type: "manual" }}
                onEditTrigger={vi.fn()}
                supportedNodeTypes={new Set(["send_email", "exit"])}
            />,
        )

        expect(screen.getByText("Send Email")).toBeInTheDocument()
        expect(screen.getByText("Exit")).toBeInTheDocument()
        expect(screen.queryByText("Send SMS")).not.toBeInTheDocument()
        expect(screen.queryByText("Update Appointment")).not.toBeInTheDocument()
    })
})
