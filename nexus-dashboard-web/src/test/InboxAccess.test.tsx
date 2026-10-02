/**
 * Who can open the conversation inbox.
 *
 * Super admins operate the platform, not a clinic, so they must not reach
 * patient conversations. The route uses the shared INBOX_ROLES list; this test
 * renders the real RoleGuard with it. The API refuses the role conversation
 * content too (tests/unit/test_inbox_service.py), so this is the second
 * barrier, not the only one.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"

import RoleGuard from "@/components/RoleGuard"
import { INBOX_ROLES } from "@/lib/inbox-access"
import type { User } from "@/types"

let currentRole: User["role"] = "SUPER_ADMIN"

vi.mock("@/context/AuthContext", () => ({
    useAuth: () => ({ user: { id: "u-1", role: currentRole } }),
}))

function renderAt(path: string) {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <Routes>
                <Route
                    path="/inbox"
                    element={
                        <RoleGuard allowed={INBOX_ROLES}>
                            <div>inbox page</div>
                        </RoleGuard>
                    }
                />
                <Route path="/admin" element={<div>super admin home</div>} />
            </Routes>
        </MemoryRouter>,
    )
}

describe("Inbox access", () => {
    beforeEach(() => {
        currentRole = "SUPER_ADMIN"
    })

    it("does not list SUPER_ADMIN among the inbox roles", () => {
        expect(INBOX_ROLES).not.toContain("SUPER_ADMIN")
    })

    it("sends a super admin who opens /inbox back to their home page", () => {
        renderAt("/inbox")

        expect(screen.queryByText("inbox page")).toBeNull()
        expect(screen.getByText("super admin home")).toBeTruthy()
    })

    it.each(["GROUP_ADMIN", "INSTITUTION_ADMIN", "LOCATION_ADMIN", "STAFF"] as const)(
        "still lets %s open the inbox",
        (role) => {
            currentRole = role
            renderAt("/inbox")

            expect(screen.getByText("inbox page")).toBeTruthy()
        },
    )
})
