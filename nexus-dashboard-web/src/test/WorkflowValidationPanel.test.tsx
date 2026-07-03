import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import WorkflowValidationPanel from "@/components/workflow/WorkflowValidationPanel"
import type { ValidationIssue } from "@/types/workflow"

describe("WorkflowValidationPanel", () => {
    it("shows an all-clear state with no issues", () => {
        render(<WorkflowValidationPanel issues={[]} onSelectNode={() => {}} />)
        expect(screen.getByText(/all checks passed/i)).toBeInTheDocument()
    })

    it("summarizes error and warning counts", () => {
        const issues: ValidationIssue[] = [
            { node_id: "sms-1", severity: "error", message: "SMS message body is empty." },
            { node_id: "sms-1", severity: "warning", message: "Unknown merge field(s): {{x}}." },
        ]
        render(<WorkflowValidationPanel issues={issues} onSelectNode={() => {}} />)
        expect(screen.getByText(/1 error/i)).toBeInTheDocument()
        expect(screen.getByText(/1 warning/i)).toBeInTheDocument()
        expect(screen.getByText("SMS message body is empty.")).toBeInTheDocument()
    })

    it("selects the node when a node-linked issue is clicked", async () => {
        const onSelect = vi.fn()
        const issues: ValidationIssue[] = [
            { node_id: "cond-1", severity: "error", message: "Condition (No branch) is not connected." },
        ]
        const user = userEvent.setup()
        render(<WorkflowValidationPanel issues={issues} onSelectNode={onSelect} />)
        await user.click(screen.getByText("Condition (No branch) is not connected."))
        expect(onSelect).toHaveBeenCalledWith("cond-1")
    })

    it("does not make graph-level issues clickable", async () => {
        const onSelect = vi.fn()
        const issues: ValidationIssue[] = [
            { node_id: null, severity: "error", message: "Workflow must have at least one Exit step." },
        ]
        const user = userEvent.setup()
        render(<WorkflowValidationPanel issues={issues} onSelectNode={onSelect} />)
        await user.click(screen.getByText("Workflow must have at least one Exit step."))
        expect(onSelect).not.toHaveBeenCalled()
    })
})
