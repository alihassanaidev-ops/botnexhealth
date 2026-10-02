import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PasswordInput } from "@/components/ui/password-input"

describe("PasswordInput", () => {
    it("starts hidden and toggles between hidden and shown", async () => {
        render(<PasswordInput aria-label="Password" defaultValue="s3cret" />)
        const input = screen.getByLabelText("Password") as HTMLInputElement

        expect(input.type).toBe("password")

        await userEvent.click(screen.getByRole("button", { name: "Show password" }))
        expect(input.type).toBe("text")
        expect(input.value).toBe("s3cret")

        await userEvent.click(screen.getByRole("button", { name: "Hide password" }))
        expect(input.type).toBe("password")
    })

    it("never submits the form it sits in", async () => {
        const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault())
        render(
            <form onSubmit={onSubmit}>
                <PasswordInput aria-label="Password" />
            </form>,
        )

        await userEvent.click(screen.getByRole("button", { name: "Show password" }))
        expect(onSubmit).not.toHaveBeenCalled()
    })

    it("passes id, aria attributes and the ref to the real input", () => {
        const ref = { current: null as HTMLInputElement | null }
        render(
            <PasswordInput
                ref={ref}
                id="pw"
                aria-describedby="pw-hint"
                aria-invalid
                autoComplete="current-password"
            />,
        )
        const input = document.getElementById("pw") as HTMLInputElement

        expect(ref.current).toBe(input)
        expect(input.getAttribute("aria-describedby")).toBe("pw-hint")
        expect(input.getAttribute("aria-invalid")).toBe("true")
        expect(input.autocomplete).toBe("current-password")
    })

    it("disables the toggle with the field", () => {
        render(<PasswordInput aria-label="Password" disabled />)
        expect(
            (screen.getByRole("button", { name: "Show password" }) as HTMLButtonElement).disabled,
        ).toBe(true)
    })
})
