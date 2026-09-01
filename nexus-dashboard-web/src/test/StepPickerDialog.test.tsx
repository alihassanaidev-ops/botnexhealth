/**
 * The step picker behind the `+`.
 *
 * It is the palette's catalogue reached by clicking rather than dragging, so
 * the things worth pinning are: it offers the same grouped list, it filters,
 * and it shows steps the clinic cannot run as disabled rather than hiding them
 * — "email is unavailable here" is useful, "email does not exist" is not.
 */
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import StepPickerDialog from "@/components/workflow/StepPickerDialog"

function open(props: Partial<React.ComponentProps<typeof StepPickerDialog>> = {}) {
    const onPick = vi.fn()
    render(
        <StepPickerDialog
            open
            onOpenChange={vi.fn()}
            onPick={onPick}
            {...props}
        />,
    )
    return onPick
}

describe("StepPickerDialog", () => {
    it("offers the catalogue, grouped as the palette is", () => {
        open()

        expect(screen.getByText("Channels")).toBeInTheDocument()
        expect(screen.getByText("Control flow")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /Send SMS/ })).toBeInTheDocument()
    })

    it("filters by label and by description", async () => {
        open()

        await userEvent.type(screen.getByLabelText("Search steps"), "switch")

        expect(screen.getByRole("button", { name: /Switch/ })).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: /Send SMS/ })).not.toBeInTheDocument()
    })

    it("says so when nothing matches", async () => {
        open()

        await userEvent.type(screen.getByLabelText("Search steps"), "zzzz")

        expect(screen.getByText(/No steps match/)).toBeInTheDocument()
    })

    it("returns the chosen step type", async () => {
        const onPick = open()

        await userEvent.click(screen.getByRole("button", { name: /Send SMS/ }))

        expect(onPick).toHaveBeenCalledWith("send_sms")
    })

    it("disables a step this clinic cannot run, rather than hiding it", () => {
        open({ supportedNodeTypes: new Set(["send_sms"]) })

        expect(screen.getByRole("button", { name: /Send Email/ })).toBeDisabled()
        expect(screen.getByRole("button", { name: /Send SMS/ })).toBeEnabled()
    })

    it("names the branch it is extending", () => {
        open({ portLabel: "No" })

        expect(screen.getByText('Runs on the "No" branch.')).toBeInTheDocument()
    })
})
