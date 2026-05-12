/**
 * Integration tests for the invite/reset password flow + MFA.
 *
 * Reproduces the exact bug Codex flagged: backend now returns an MFA
 * challenge from /auth/reset-password and /auth/set-password, and the
 * frontend must render the MFA setup/verify UI instead of dropping the
 * response on the floor.
 *
 * The tests stub the network seam (axios for the password endpoints,
 * @/lib/mfa-api for the MFA endpoints) and the @simplewebauthn helpers.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import SetPassword from "@/pages/SetPassword"
import { AuthProvider } from "@/context/AuthContext"
import api from "@/lib/api"
import * as mfaApi from "@/lib/mfa-api"

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
    getAccessToken: () => null,
    setAccessToken: vi.fn(),
    clearAccessToken: vi.fn(),
}))

vi.mock("sonner", () => ({
    toast: { error: vi.fn(), success: vi.fn(), message: vi.fn() },
    Toaster: () => null,
}))

vi.mock("qrcode.react", () => ({
    QRCodeSVG: ({ value }: { value: string }) => (
        <div data-testid="qr-code" data-value={value} />
    ),
}))

vi.mock("@simplewebauthn/browser", async () => ({
    startRegistration: vi.fn(),
    startAuthentication: vi.fn(),
    browserSupportsWebAuthn: vi.fn(() => true),
}))

vi.mock("@/lib/mfa-api", () => ({
    loginWithPassword: vi.fn(),
    startTotpSetup: vi.fn(),
    verifyTotpSetup: vi.fn(),
    verifyTotp: vi.fn(),
    verifyRecoveryCode: vi.fn(),
    startWebauthnRegistration: vi.fn(),
    verifyWebauthnRegistration: vi.fn(),
    startWebauthnAuthentication: vi.fn(),
    verifyWebauthnAuthentication: vi.fn(),
}))

const { axiosPostMock } = vi.hoisted(() => ({ axiosPostMock: vi.fn() }))
vi.mock("axios", async (importOriginal) => {
    const real = await importOriginal<typeof import("axios")>()
    return {
        default: {
            ...real.default,
            post: axiosPostMock,
            isAxiosError: real.default.isAxiosError,
        },
    }
})

const mockedNavigate = vi.fn()
vi.mock("react-router-dom", async (orig) => {
    const real = await orig<typeof import("react-router-dom")>()
    return {
        ...real,
        useNavigate: () => mockedNavigate,
        useLocation: () => ({
            pathname: "/set-password",
            state: null,
            search: "?token=invite-abc&flow=invite",
            hash: "",
            key: "",
        }),
        useSearchParams: () => [
            new URLSearchParams("token=invite-abc&flow=invite"),
            vi.fn(),
        ],
    }
})

const apiGet = api.get as ReturnType<typeof vi.fn>

function renderSetPassword() {
    return render(
        <MemoryRouter initialEntries={["/set-password?token=invite-abc&flow=invite"]}>
            <AuthProvider>
                <SetPassword />
            </AuthProvider>
        </MemoryRouter>,
    )
}

beforeEach(() => {
    axiosPostMock.mockReset()
    apiGet.mockReset()
    mockedNavigate.mockReset()
    Object.values(mfaApi).forEach((fn) => {
        if (typeof fn === "function" && "mockReset" in fn) {
            ;(fn as ReturnType<typeof vi.fn>).mockReset()
        }
    })
    apiGet.mockRejectedValue({ response: { status: 401 } })
})

async function submitPasswordForm(password = "Aaaaaaa1!") {
    const user = userEvent.setup()
    await waitFor(() => expect(screen.getByLabelText(/^New Password$/i)).toBeInTheDocument())
    await user.type(screen.getByLabelText(/^New Password$/i), password)
    await user.type(screen.getByLabelText(/Confirm Password/i), password)
    await user.click(screen.getByRole("button", { name: /Set Password/i }))
    return user
}

describe("SetPassword — MFA continuation after invite/reset", () => {
    it("invited user with no MFA: password POST returns mfa_setup_required, page renders setup chooser", async () => {
        axiosPostMock.mockResolvedValue({
            data: {
                status: "mfa_setup_required",
                mfa_ticket: "ticket-sp1",
                methods: [],
                setup_methods: ["webauthn", "totp"],
                expires_in_seconds: 600,
                role: "INSTITUTION_ADMIN",
                email: "newadmin@clinic.test",
            },
        })

        renderSetPassword()
        await submitPasswordForm()

        // Password form is gone; the MFA setup UI is up — and the
        // setup chooser is the right entry point for a user with both
        // setup methods available.
        await waitFor(() => {
            expect(screen.getByText(/Set up two-factor/i)).toBeInTheDocument()
        })
        expect(screen.getByRole("button", { name: /Use a passkey/i })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /Use an authenticator app/i })).toBeInTheDocument()
        // Crucial bug fix check: navigate was NOT called — the old
        // (broken) path would have applied a bad session and pushed to
        // "/", landing the user on a 401 dashboard.
        expect(mockedNavigate).not.toHaveBeenCalled()
    })

    it("reset flow lands a user with an existing factor onto verify (passkey by default)", async () => {
        axiosPostMock.mockResolvedValue({
            data: {
                status: "mfa_required",
                mfa_ticket: "ticket-sp2",
                methods: ["webauthn", "totp"],
                setup_methods: [],
                expires_in_seconds: 600,
                role: "INSTITUTION_ADMIN",
                email: "alice@clinic.test",
            },
        })

        renderSetPassword()
        await submitPasswordForm()

        await waitFor(() => {
            expect(screen.getByText(/Two-factor verification/i)).toBeInTheDocument()
        })
        // Default mode is passkey when the user has one — same logic
        // the login flow uses.
        expect(screen.getByRole("button", { name: /Verify with passkey/i })).toBeInTheDocument()
    })

    it("setup -> TOTP -> verify dispatches verifyTotpSetup with the ticket from the password response", async () => {
        axiosPostMock.mockResolvedValue({
            data: {
                status: "mfa_setup_required",
                mfa_ticket: "ticket-sp3",
                methods: [],
                setup_methods: ["webauthn", "totp"],
                expires_in_seconds: 600,
                role: "STAFF",
                email: "bob@clinic.test",
            },
        })
        ;(mfaApi.startTotpSetup as ReturnType<typeof vi.fn>).mockResolvedValue({
            secret: "SECRETSECRET",
            provisioning_uri: "otpauth://totp/x:y?secret=SECRETSECRET",
        })
        ;(mfaApi.verifyTotpSetup as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "authenticated",
            access_token: "jwt-sp",
            token_type: "bearer",
            recovery_codes: ["rec-a", "rec-b"],
        })

        const user = await (async () => {
            renderSetPassword()
            return submitPasswordForm()
        })()
        await waitFor(() => {
            expect(screen.getByText(/Set up two-factor/i)).toBeInTheDocument()
        })
        await user.click(screen.getByRole("button", { name: /Use an authenticator app/i }))
        await waitFor(() => {
            expect(screen.getByTestId("qr-code")).toHaveAttribute(
                "data-value",
                "otpauth://totp/x:y?secret=SECRETSECRET",
            )
        })
        expect(mfaApi.startTotpSetup).toHaveBeenCalledWith("ticket-sp3")

        await user.type(screen.getByLabelText(/6-digit code/i), "012345")
        await user.click(screen.getByRole("button", { name: /verify and continue/i }))

        expect(mfaApi.verifyTotpSetup).toHaveBeenCalledWith("ticket-sp3", "012345")
        // The shared MfaFlow renders the recovery codes inline within
        // the existing card; assert on the code strings + the "Copy
        // codes" affordance, which are the user-facing proof that this
        // is the recovery-codes step.
        await waitFor(() => {
            expect(screen.getByText("rec-a")).toBeInTheDocument()
        })
        expect(screen.getByText("rec-b")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: /Copy codes/i })).toBeInTheDocument()
    })

    it("password endpoint that returns AuthSession directly (no-MFA path, legacy) navigates to /", async () => {
        axiosPostMock.mockResolvedValue({
            data: {
                access_token: "jwt-direct",
                token_type: "bearer",
            },
        })
        apiGet.mockResolvedValue({
            data: {
                id: "u",
                email: "x@y.com",
                role: "STAFF",
                institution_id: "i",
                is_active: true,
            },
        })

        renderSetPassword()
        await submitPasswordForm()

        await waitFor(() => {
            expect(mockedNavigate).toHaveBeenCalledWith("/", { replace: true })
        })
        // No MFA UI was rendered because the response didn't carry the
        // challenge status field. This is the defensive branch — today
        // the backend always issues a challenge, but we don't want a
        // future opt-out to silently break this page.
        expect(screen.queryByText(/Set up two-factor/i)).toBeNull()
        expect(screen.queryByText(/Two-factor verification/i)).toBeNull()
    })
})
