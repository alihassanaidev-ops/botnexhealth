import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import WorkflowValidationPanel from "@/components/workflow/WorkflowValidationPanel"
import type { ChannelStatus } from "@/lib/workflow/readiness"
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

    it("renders backend compliance issues in a distinct 'Server' section", () => {
        const backendIssues: ValidationIssue[] = [
            { node_id: null, severity: "warning", message: "Content class is not set.", code: "content_class_unset" },
            { node_id: "sms-1", severity: "error", message: "Consent is required for this channel.", code: "consent_required" },
        ]
        render(<WorkflowValidationPanel issues={[]} backendIssues={backendIssues} onSelectNode={() => {}} />)
        expect(screen.getByText(/server & compliance checks/i)).toBeInTheDocument()
        expect(screen.getByText("Content class is not set.")).toBeInTheDocument()
        expect(screen.getByText("Consent is required for this channel.")).toBeInTheDocument()
        // Combined counts include backend issues.
        expect(screen.getByText(/1 error/i)).toBeInTheDocument()
        expect(screen.getByText(/1 warning/i)).toBeInTheDocument()
        // Server badge distinguishes them from client issues.
        expect(screen.getAllByText("Server").length).toBeGreaterThan(0)
    })

    it("stays all-clear only when both client and backend issues are empty", () => {
        render(<WorkflowValidationPanel issues={[]} backendIssues={[]} onSelectNode={() => {}} />)
        expect(screen.getByText(/all checks passed/i)).toBeInTheDocument()
    })

    it("warns when a channel the workflow uses is not ready for its location", () => {
        const readiness: ChannelStatus[] = [
            { channel: "sms", label: "SMS", ready: true, reason: null },
            {
                channel: "voice",
                label: "Voice",
                ready: false,
                reason: "No outbound voice agent is configured for this location.",
            },
        ]
        render(<WorkflowValidationPanel issues={[]} readiness={readiness} onSelectNode={() => {}} />)
        expect(screen.getByText(/channel readiness/i)).toBeInTheDocument()
        expect(screen.getByText("Voice is not set up for this location.")).toBeInTheDocument()
        expect(
            screen.getByText("No outbound voice agent is configured for this location."),
        ).toBeInTheDocument()
        // The unready channel feeds the combined warning count.
        expect(screen.getByText(/1 warning/i)).toBeInTheDocument()
    })

    it("shows the readiness indicator but no warning when every used channel is ready", () => {
        const readiness: ChannelStatus[] = [
            { channel: "sms", label: "SMS", ready: true, reason: null },
            { channel: "email", label: "Email", ready: true, reason: null },
        ]
        render(<WorkflowValidationPanel issues={[]} readiness={readiness} onSelectNode={() => {}} />)
        expect(screen.getByText(/channel readiness/i)).toBeInTheDocument()
        expect(screen.queryByText(/is not set up for this location/i)).not.toBeInTheDocument()
        expect(screen.getByText(/all checks passed/i)).toBeInTheDocument()
    })

    it("shows no readiness section when the workflow has no location (readiness omitted)", () => {
        render(<WorkflowValidationPanel issues={[]} onSelectNode={() => {}} />)
        expect(screen.queryByText(/channel readiness/i)).not.toBeInTheDocument()
        expect(screen.getByText(/all checks passed/i)).toBeInTheDocument()
    })
})
