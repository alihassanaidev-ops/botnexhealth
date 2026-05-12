/**
 * Real integration tests for /security — MFA management page.
 *
 * Stubs the network seam at @/lib/security-api and the @simplewebauthn
 * helpers, then renders the actual page wrapped in AuthProvider +
 * MemoryRouter. Drives the destructive flows via userEvent and asserts:
 *
 *   - Read endpoints fire on mount with the right shape.
 *   - Each destructive action triggers a step-up modal first
 *     (no API call to the destructive endpoint until the modal
 *     resolves an elevated ticket).
 *   - The destructive endpoint receives the elevated ticket the
 *     step-up verify call returned.
 *   - On regenerate, the new recovery codes render inline.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import Security from "@/pages/Security"
import { AuthProvider } from "@/context/AuthContext"
import api from "@/lib/api"
import * as securityApi from "@/lib/security-api"
import * as webauthn from "@simplewebauthn/browser"

vi.mock("@/lib/api", () => ({
    default: {
        defaults: { baseURL: "http://test.local/api" },
        get: vi.fn(),
        post: vi.fn(),
        patch: vi.fn(),
        delete: vi.fn(),
        interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    },
}))

vi.mock("@/lib/token-manager", () => ({
    getAccessToken: () => "fake-token",
    setAccessToken: vi.fn(),
    clearAccessToken: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn(), message: vi.fn() },
    Toaster: () => null,
}))

vi.mock("@simplewebauthn/browser", async () => ({
    startAuthentication: vi.fn(),
    startRegistration: vi.fn(),
    browserSupportsWebAuthn: vi.fn(() => true),
}))

vi.mock("qrcode.react", () => ({
    QRCodeSVG: ({ value }: { value: string }) => (
        <div data-testid="add-totp-qr" data-value={value} />
    ),
}))

vi.mock("@/lib/security-api", () => ({
    getMfaStatus: vi.fn(),
    listPasskeys: vi.fn(),
    regenerateRecoveryCodes: vi.fn(),
    removePasskey: vi.fn(),
    disableTotp: vi.fn(),
    startStepUp: vi.fn(),
    stepUpVerifyTotp: vi.fn(),
    stepUpVerifyRecoveryCode: vi.fn(),
    stepUpWebauthnOptions: vi.fn(),
    stepUpWebauthnVerify: vi.fn(),
    addPasskeyOptions: vi.fn(),
    addPasskeyVerify: vi.fn(),
    addTotpOptions: vi.fn(),
    addTotpVerify: vi.fn(),
}))

const apiGet = api.get as ReturnType<typeof vi.fn>

const mockedNavigate = vi.fn()
vi.mock("react-router-dom", async (orig) => {
    const real = await orig<typeof import("react-router-dom")>()
    return {
        ...real,
        useNavigate: () => mockedNavigate,
        useLocation: () => ({ pathname: "/security", state: null, search: "", hash: "", key: "" }),
    }
})

function renderSecurity() {
    return render(
        <MemoryRouter initialEntries={["/security"]}>
            <AuthProvider>
                <Security />
            </AuthProvider>
        </MemoryRouter>,
    )
}

beforeEach(() => {
    apiGet.mockReset()
    mockedNavigate.mockReset()
    Object.values(securityApi).forEach((fn) => {
        if (typeof fn === "function" && "mockReset" in fn) {
            ;(fn as ReturnType<typeof vi.fn>).mockReset()
        }
    })
    apiGet.mockResolvedValue({
        data: { id: "u", email: "x@y.com", role: "INSTITUTION_ADMIN", institution_id: "i", is_active: true },
    })
})

function defaultStubs() {
    ;(securityApi.getMfaStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
        webauthn_count: 1,
        totp_enabled: true,
        recovery_codes_remaining: 7,
        methods: ["webauthn", "totp", "recovery_code"],
    })
    ;(securityApi.listPasskeys as ReturnType<typeof vi.fn>).mockResolvedValue([
        {
            id: "pk-1",
            device_label: "MacBook Pro",
            aaguid: null,
            credential_device_type: "multi_device",
            credential_backed_up: true,
            transports: ["internal"],
            created_at: "2026-04-15T10:00:00Z",
            last_used_at: "2026-05-10T09:00:00Z",
        },
    ])
}

describe("Security page — read endpoints", () => {
    it("renders MFA status, passkey list, and TOTP enabled badge on mount", async () => {
        defaultStubs()

        renderSecurity()

        await waitFor(() => {
            expect(screen.getByText(/Security/)).toBeInTheDocument()
        })
        // Passkey row visible with its device label.
        expect(screen.getByText("MacBook Pro")).toBeInTheDocument()
        // TOTP enabled badge.
        expect(screen.getByText(/Enabled/i)).toBeInTheDocument()
        // Recovery codes count.
        expect(screen.getByText(/7 unused/i)).toBeInTheDocument()
    })
})

describe("Security page — step-up gating", () => {
    it("regenerate recovery codes: opens step-up, then calls regenerate with the elevated ticket", async () => {
        defaultStubs()
        ;(securityApi.startStepUp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_required",
            mfa_ticket: "challenge-token",
            methods: ["totp"],
            setup_methods: [],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "x@y.com",
        })
        ;(securityApi.stepUpVerifyTotp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_complete",
            mfa_ticket: "elevated-token",
            expires_in_seconds: 90,
        })
        ;(securityApi.regenerateRecoveryCodes as ReturnType<typeof vi.fn>).mockResolvedValue([
            "code-1", "code-2", "code-3",
        ])

        renderSecurity()
        const user = userEvent.setup()
        await waitFor(() => expect(screen.getByText("MacBook Pro")).toBeInTheDocument())

        await user.click(screen.getByRole("button", { name: /Regenerate codes/i }))

        // Step-up dialog is up; regenerate has NOT been called yet.
        await waitFor(() => {
            expect(screen.getByText(/Regenerate recovery codes/i)).toBeInTheDocument()
        })
        expect(securityApi.regenerateRecoveryCodes).not.toHaveBeenCalled()
        expect(securityApi.startStepUp).toHaveBeenCalledTimes(1)

        // User enters their TOTP code in the step-up modal.
        await user.type(screen.getByLabelText(/6-digit code/i), "987654")
        await user.click(screen.getByRole("button", { name: /^Confirm$/i }))

        // Verify call carries the challenge ticket; the elevated ticket
        // it returned is what the destructive endpoint receives.
        await waitFor(() => {
            expect(securityApi.stepUpVerifyTotp).toHaveBeenCalledWith(
                "challenge-token",
                "987654",
            )
        })
        await waitFor(() => {
            expect(securityApi.regenerateRecoveryCodes).toHaveBeenCalledWith("elevated-token")
        })

        // New codes render inline so the user can save them.
        await waitFor(() => {
            expect(screen.getByText("code-1")).toBeInTheDocument()
        })
        expect(screen.getByText("code-2")).toBeInTheDocument()
        expect(screen.getByText("code-3")).toBeInTheDocument()
    })

    it("remove passkey: opens step-up first, then deletes with elevated ticket", async () => {
        defaultStubs()
        ;(securityApi.startStepUp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_required",
            mfa_ticket: "challenge-token-2",
            methods: ["totp"],
            setup_methods: [],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "x@y.com",
        })
        ;(securityApi.stepUpVerifyTotp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_complete",
            mfa_ticket: "elevated-rm",
            expires_in_seconds: 90,
        })
        ;(securityApi.removePasskey as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)

        renderSecurity()
        const user = userEvent.setup()
        await waitFor(() => expect(screen.getByText("MacBook Pro")).toBeInTheDocument())

        // Click the per-row remove button.
        const row = screen.getByTestId("passkey-row")
        const removeBtn = row.querySelector("button")!
        await user.click(removeBtn)

        // Step-up appears; remove not yet called.
        await waitFor(() => {
            expect(screen.getByText(/Remove.*MacBook Pro/i)).toBeInTheDocument()
        })
        expect(securityApi.removePasskey).not.toHaveBeenCalled()

        await user.type(screen.getByLabelText(/6-digit code/i), "111222")
        await user.click(screen.getByRole("button", { name: /^Confirm$/i }))

        await waitFor(() => {
            expect(securityApi.removePasskey).toHaveBeenCalledWith("pk-1", "elevated-rm")
        })
    })

    it("disable TOTP: step-up gate then disable endpoint with elevated ticket", async () => {
        defaultStubs()
        ;(securityApi.startStepUp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_required",
            mfa_ticket: "challenge-token-3",
            methods: ["webauthn", "totp"],
            setup_methods: [],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "x@y.com",
        })
        ;(securityApi.stepUpVerifyTotp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_complete",
            mfa_ticket: "elevated-disable",
            expires_in_seconds: 90,
        })
        ;(securityApi.disableTotp as ReturnType<typeof vi.fn>).mockResolvedValue("Authenticator app disabled")

        renderSecurity()
        const user = userEvent.setup()
        await waitFor(() => expect(screen.getByText("MacBook Pro")).toBeInTheDocument())

        await user.click(screen.getByRole("button", { name: /^Disable$/i }))

        await waitFor(() => {
            expect(screen.getByText(/Disable authenticator app/i)).toBeInTheDocument()
        })
        // Multi-factor account defaults to passkey in the step-up
        // dialog — switch to TOTP first.
        await user.click(screen.getByRole("button", { name: /Use authenticator code instead/i }))
        await user.type(screen.getByLabelText(/6-digit code/i), "777888")
        await user.click(screen.getByRole("button", { name: /^Confirm$/i }))

        await waitFor(() => {
            expect(securityApi.disableTotp).toHaveBeenCalledWith("elevated-disable")
        })
    })

    it("cancelling step-up does NOT call the destructive endpoint", async () => {
        defaultStubs()
        ;(securityApi.startStepUp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_required",
            mfa_ticket: "challenge-token-4",
            methods: ["totp"],
            setup_methods: [],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "x@y.com",
        })

        renderSecurity()
        const user = userEvent.setup()
        await waitFor(() => expect(screen.getByText("MacBook Pro")).toBeInTheDocument())

        await user.click(screen.getByRole("button", { name: /Regenerate codes/i }))
        await waitFor(() => {
            expect(screen.getByText(/Regenerate recovery codes/i)).toBeInTheDocument()
        })
        await user.click(screen.getByRole("button", { name: /Cancel/i }))

        expect(securityApi.stepUpVerifyTotp).not.toHaveBeenCalled()
        expect(securityApi.regenerateRecoveryCodes).not.toHaveBeenCalled()
    })
})


describe("Security page — add factor flows", () => {
    it("Add passkey: step-up → enrollment options → browser registration → verify", async () => {
        defaultStubs()
        ;(securityApi.getMfaStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
            webauthn_count: 0,
            totp_enabled: true,
            recovery_codes_remaining: 8,
            methods: ["totp", "recovery_code"],
        })
        ;(securityApi.listPasskeys as ReturnType<typeof vi.fn>).mockResolvedValue([])

        ;(securityApi.startStepUp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_required",
            mfa_ticket: "challenge-add-pk",
            methods: ["totp"],
            setup_methods: [],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "x@y.com",
        })
        ;(securityApi.stepUpVerifyTotp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_complete",
            mfa_ticket: "elevated-add-pk",
            expires_in_seconds: 90,
        })
        ;(securityApi.addPasskeyOptions as ReturnType<typeof vi.fn>).mockResolvedValue({
            enrollment_ticket: "enroll-pk",
            options: { challenge: "Y2hh", rp: { id: "x" } },
            expires_in_seconds: 300,
        })
        const credentialJson = {
            id: "cred-id",
            rawId: "cred-id",
            response: { attestationObject: "ao", clientDataJSON: "cdj" },
            type: "public-key",
            clientExtensionResults: {},
        }
        ;(webauthn.startRegistration as ReturnType<typeof vi.fn>).mockResolvedValue(credentialJson)
        ;(securityApi.addPasskeyVerify as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "registered",
            credential: {
                id: "new-cred-pk", device_label: null, aaguid: null,
                credential_device_type: "multi_device", credential_backed_up: true,
                transports: ["internal"],
                created_at: "2026-05-11T00:00:00Z", last_used_at: null,
            },
        })

        renderSecurity()
        const user = userEvent.setup()
        await waitFor(() => expect(screen.getByRole("button", { name: /Add passkey/i })).toBeInTheDocument())

        await user.click(screen.getByRole("button", { name: /Add passkey/i }))

        // First the step-up dialog opens.
        await waitFor(() => {
            expect(screen.getByText(/Add a passkey/i)).toBeInTheDocument()
        })
        await user.type(screen.getByLabelText(/6-digit code/i), "654321")
        await user.click(screen.getByRole("button", { name: /^Confirm$/i }))

        // Then the label-entry dialog opens — user can name the
        // passkey BEFORE the browser prompt fires. This is the bug
        // Codex flagged: previously the input was disabled because
        // startRegistration was already in flight.
        await waitFor(() => {
            expect(screen.getByLabelText(/Device name/i)).toBeInTheDocument()
        })
        // No browser prompt yet at this stage.
        expect(webauthn.startRegistration).not.toHaveBeenCalled()
        expect(securityApi.addPasskeyOptions).not.toHaveBeenCalled()

        // User types the label and clicks Continue.
        await user.type(screen.getByLabelText(/Device name/i), "MacBook Pro")
        await user.click(screen.getByRole("button", { name: /^Continue$/i }))

        await waitFor(() => {
            expect(securityApi.addPasskeyOptions).toHaveBeenCalledWith("elevated-add-pk")
        })
        await waitFor(() => {
            expect(webauthn.startRegistration).toHaveBeenCalledWith({
                optionsJSON: expect.objectContaining({ challenge: "Y2hh" }),
            })
        })
        // The label the user typed makes it onto the verify call —
        // regression for the empty-label bug.
        await waitFor(() => {
            expect(securityApi.addPasskeyVerify).toHaveBeenCalledWith(
                "enroll-pk",
                credentialJson,
                "MacBook Pro",
            )
        })
    })

    it("Add authenticator app (when not yet enabled): renders QR + posts code to verify", async () => {
        defaultStubs()
        ;(securityApi.getMfaStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
            webauthn_count: 1,
            totp_enabled: false,
            recovery_codes_remaining: 8,
            methods: ["webauthn", "recovery_code"],
        })

        ;(securityApi.startStepUp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_required",
            mfa_ticket: "challenge-add-totp",
            methods: ["webauthn"],
            setup_methods: [],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "x@y.com",
        })
        ;(securityApi.stepUpWebauthnOptions as ReturnType<typeof vi.fn>).mockResolvedValue({
            options: { challenge: "Y2hh", rpId: "x" },
        })
        ;(securityApi.stepUpWebauthnVerify as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "step_up_complete",
            mfa_ticket: "elevated-add-totp",
            expires_in_seconds: 90,
        })
        ;(webauthn.startAuthentication as ReturnType<typeof vi.fn>).mockResolvedValue({
            id: "x", rawId: "x", response: {}, type: "public-key", clientExtensionResults: {},
        })
        ;(securityApi.addTotpOptions as ReturnType<typeof vi.fn>).mockResolvedValue({
            enrollment_ticket: "enroll-totp",
            secret: "ABCDEFGHJKLMNPQR",
            provisioning_uri: "otpauth://totp/y:x@y.com?secret=ABCDEFGHJKLMNPQR",
            expires_in_seconds: 300,
        })
        ;(securityApi.addTotpVerify as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "enrolled",
            totp_enabled: true,
        })

        renderSecurity()
        const user = userEvent.setup()
        await waitFor(() => {
            expect(screen.getByRole("button", { name: /Set up authenticator/i })).toBeInTheDocument()
        })

        await user.click(screen.getByRole("button", { name: /Set up authenticator/i }))

        await waitFor(() => {
            expect(screen.getByText(/Add authenticator app/i)).toBeInTheDocument()
        })
        await user.click(screen.getByRole("button", { name: /Verify with passkey/i }))

        await waitFor(() => {
            expect(screen.getByTestId("add-totp-qr")).toBeInTheDocument()
        })
        expect(securityApi.addTotpOptions).toHaveBeenCalledWith("elevated-add-totp")
        expect(screen.getByText("ABCDEFGHJKLMNPQR")).toBeInTheDocument()

        await user.type(screen.getByLabelText(/6-digit code/i), "000111")
        await user.click(screen.getByRole("button", { name: /Verify and enable/i }))

        await waitFor(() => {
            expect(securityApi.addTotpVerify).toHaveBeenCalledWith("enroll-totp", "000111")
        })
    })
})
