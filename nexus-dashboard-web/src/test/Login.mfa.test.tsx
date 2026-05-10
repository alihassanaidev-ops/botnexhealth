/**
 * Real integration tests for the MFA login flow.
 *
 * Stubs the network seam at @/lib/mfa-api and the @simplewebauthn/browser
 * helpers (the only browser API jsdom can't provide), then renders the
 * actual Login component and AuthContext and drives them via userEvent.
 *
 * What the tests pin:
 *   - Each branch of the role/methods matrix lands the user on the
 *     right screen (super-admin → passkey-only; mixed user → choose
 *     screen).
 *   - The right mfa-api function is called with the right arguments
 *     (mfa_ticket threading; entered codes; passkey credentials passed
 *     through unmodified).
 *   - Recovery codes screen shows exactly the codes the backend
 *     returned.
 */

import { describe, it, expect, beforeEach, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"

import Login from "@/pages/Login"
import { AuthProvider } from "@/context/AuthContext"
import api from "@/lib/api"
import * as mfaApi from "@/lib/mfa-api"
import * as webauthn from "@simplewebauthn/browser"

// api.ts is mocked so AuthContext bootstrap doesn't issue real /users/me.
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

// jsdom can't run navigator.credentials, so mock the two helpers
// Login.tsx imports plus the support detector.
vi.mock("@simplewebauthn/browser", async () => ({
    startRegistration: vi.fn(),
    startAuthentication: vi.fn(),
    browserSupportsWebAuthn: vi.fn(() => true),
}))

// Mock the API module that Login uses for both password + MFA. AuthContext
// also calls /auth/login via axios.post directly (legacy path) — we
// override that by mocking signIn directly through the API surface.
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

// AuthContext's signIn issues axios.post to /auth/login directly. Stub
// the global axios so the call hits a vi.fn we can drive per test.
// vi.hoisted is required because vi.mock() factories run before any
// module-level `const` initializer.
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

const apiGet = api.get as ReturnType<typeof vi.fn>
const startRegistrationMock = webauthn.startRegistration as ReturnType<typeof vi.fn>
const startAuthenticationMock = webauthn.startAuthentication as ReturnType<typeof vi.fn>

const mockedNavigate = vi.fn()
vi.mock("react-router-dom", async (orig) => {
    const real = await orig<typeof import("react-router-dom")>()
    return {
        ...real,
        useNavigate: () => mockedNavigate,
        useLocation: () => ({ pathname: "/login", state: null, search: "", hash: "", key: "" }),
    }
})

function renderLogin() {
    return render(
        <MemoryRouter initialEntries={["/login"]}>
            <AuthProvider>
                <Login />
            </AuthProvider>
        </MemoryRouter>,
    )
}

beforeEach(() => {
    axiosPostMock.mockReset()
    apiGet.mockReset()
    startRegistrationMock.mockReset()
    startAuthenticationMock.mockReset()
    mockedNavigate.mockReset()
    Object.values(mfaApi).forEach((fn) => {
        if (typeof fn === "function" && "mockReset" in fn) {
            ;(fn as ReturnType<typeof vi.fn>).mockReset()
        }
    })
    apiGet.mockRejectedValue({ response: { status: 401 } })
})

async function fillCredentialsAndSubmit(email = "alice@clinic.test", password = "correcthorse") {
    const user = userEvent.setup()
    await waitFor(() => expect(screen.getByLabelText(/email/i)).toBeInTheDocument())
    await user.type(screen.getByLabelText(/email/i), email)
    await user.type(screen.getByLabelText(/password/i), password)
    await user.click(screen.getByRole("button", { name: /^sign in$/i }))
    return user
}

function loginChallenge(overrides: Partial<{
    status: "mfa_required" | "mfa_setup_required"
    mfa_ticket: string
    methods: string[]
    setup_methods: string[]
    role: string
    email: string
}> = {}) {
    return {
        data: {
            status: "mfa_setup_required",
            mfa_ticket: "ticket-1",
            methods: [],
            setup_methods: ["webauthn", "totp"],
            expires_in_seconds: 600,
            role: "INSTITUTION_ADMIN",
            email: "alice@clinic.test",
            ...overrides,
        },
    }
}

describe("Login — MFA flow (TOTP setup)", () => {
    it("non-super-admin first login: shows choose screen, then QR, then verifies and shows recovery codes", async () => {
        axiosPostMock.mockResolvedValue(loginChallenge())
        ;(mfaApi.startTotpSetup as ReturnType<typeof vi.fn>).mockResolvedValue({
            secret: "ABCDEFGH12345678",
            provisioning_uri: "otpauth://totp/Test:alice@clinic.test?secret=ABCDEFGH12345678",
        })
        ;(mfaApi.verifyTotpSetup as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "authenticated",
            access_token: "jwt-1",
            token_type: "bearer",
            recovery_codes: ["aaaa-bbbb-cccc", "dddd-eeee-ffff"],
        })

        renderLogin()
        const user = await fillCredentialsAndSubmit()

        await waitFor(() => {
            expect(screen.getByText(/Set up two-factor/i)).toBeInTheDocument()
        })
        expect(screen.getByRole("button", { name: /Use a passkey/i })).toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: /Use an authenticator app/i }))

        await waitFor(() => {
            expect(screen.getByTestId("qr-code")).toHaveAttribute(
                "data-value",
                "otpauth://totp/Test:alice@clinic.test?secret=ABCDEFGH12345678",
            )
        })
        expect(screen.getByText("ABCDEFGH12345678")).toBeInTheDocument()
        expect(mfaApi.startTotpSetup).toHaveBeenCalledWith("ticket-1")

        await user.type(screen.getByLabelText(/6-digit code/i), "123456")
        await user.click(screen.getByRole("button", { name: /verify and continue/i }))

        await waitFor(() => {
            expect(screen.getByText(/Save your recovery codes/i)).toBeInTheDocument()
        })
        expect(screen.getByText("aaaa-bbbb-cccc")).toBeInTheDocument()
        expect(screen.getByText("dddd-eeee-ffff")).toBeInTheDocument()
        expect(mfaApi.verifyTotpSetup).toHaveBeenCalledWith("ticket-1", "123456")
    })

    it("super-admin first login: lands on passkey setup screen (TOTP not offered)", async () => {
        axiosPostMock.mockResolvedValue(
            loginChallenge({
                mfa_ticket: "ticket-sa",
                setup_methods: ["webauthn"],
                role: "SUPER_ADMIN",
                email: "root@clinic.test",
            }),
        )

        renderLogin()
        await fillCredentialsAndSubmit("root@clinic.test")

        await waitFor(() => {
            expect(screen.getByText(/Register a passkey/i)).toBeInTheDocument()
        })
        // No TOTP path because SUPER_ADMIN can't enrol it on the backend.
        expect(screen.queryByRole("button", { name: /authenticator app/i })).toBeNull()
    })
})

describe("Login — MFA flow (passkey)", () => {
    it("registers a passkey, posts the credential unmodified, and shows recovery codes", async () => {
        const credentialJson = {
            id: "cred-id",
            rawId: "cred-id",
            response: { attestationObject: "ao", clientDataJSON: "cdj" },
            type: "public-key",
            clientExtensionResults: {},
        }
        startRegistrationMock.mockResolvedValue(credentialJson)
        axiosPostMock.mockResolvedValue(loginChallenge({ mfa_ticket: "ticket-pk" }))
        ;(mfaApi.startWebauthnRegistration as ReturnType<typeof vi.fn>).mockResolvedValue({
            options: {
                rp: { id: "test.local", name: "Test" },
                user: { id: "u1", name: "alice", displayName: "Alice" },
                challenge: "Y2hhbGxlbmdl",
                pubKeyCredParams: [{ type: "public-key", alg: -7 }],
            },
        })
        ;(mfaApi.verifyWebauthnRegistration as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "authenticated",
            access_token: "jwt-2",
            token_type: "bearer",
            recovery_codes: ["rec-1", "rec-2"],
        })

        renderLogin()
        const user = await fillCredentialsAndSubmit()

        await waitFor(() => {
            expect(screen.getByText(/Set up two-factor/i)).toBeInTheDocument()
        })
        await user.click(screen.getByRole("button", { name: /Use a passkey/i }))

        await waitFor(() => {
            expect(startRegistrationMock).toHaveBeenCalledTimes(1)
        })
        // The browser library was given the exact options object the
        // backend returned — no surrogate type cast in between.
        expect(startRegistrationMock.mock.calls[0][0]).toEqual({
            optionsJSON: expect.objectContaining({ challenge: "Y2hhbGxlbmdl" }),
        })

        await waitFor(() => {
            expect(screen.getByText(/Save your recovery codes/i)).toBeInTheDocument()
        })
        // Verify endpoint receives the credential JSON unchanged plus
        // the optional device label (empty here -> undefined).
        expect(mfaApi.verifyWebauthnRegistration).toHaveBeenCalledWith(
            "ticket-pk",
            credentialJson,
            undefined,
        )
    })
})

describe("Login — MFA flow (verify path for already-enrolled user)", () => {
    it("user with passkey + TOTP defaults to passkey, can switch to TOTP", async () => {
        axiosPostMock.mockResolvedValue(
            loginChallenge({
                status: "mfa_required",
                mfa_ticket: "ticket-v1",
                methods: ["webauthn", "totp", "recovery_code"],
                setup_methods: [],
            }),
        )

        renderLogin()
        const user = await fillCredentialsAndSubmit()

        await waitFor(() => {
            expect(screen.getByText(/Two-factor verification/i)).toBeInTheDocument()
        })
        expect(screen.getByRole("button", { name: /Sign in with passkey/i })).toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: /Use authenticator code instead/i }))
        await waitFor(() => {
            expect(screen.getByLabelText(/6-digit code/i)).toBeInTheDocument()
        })
    })

    it("TOTP verify calls verifyTotp with the entered code", async () => {
        axiosPostMock.mockResolvedValue(
            loginChallenge({
                status: "mfa_required",
                mfa_ticket: "ticket-v2",
                methods: ["totp"],
                setup_methods: [],
                role: "STAFF",
                email: "bob@clinic.test",
            }),
        )
        ;(mfaApi.verifyTotp as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "authenticated",
            access_token: "jwt-3",
            token_type: "bearer",
            recovery_codes: null,
        })
        // Successful post-auth /users/me so the navigation completes.
        apiGet.mockResolvedValue({
            data: {
                id: "u1",
                email: "bob@clinic.test",
                role: "STAFF",
                institution_id: "inst-1",
                location_id: "loc-1",
                is_active: true,
            },
        })

        renderLogin()
        const user = await fillCredentialsAndSubmit("bob@clinic.test")
        await waitFor(() => {
            expect(screen.getByLabelText(/6-digit code/i)).toBeInTheDocument()
        })

        await user.type(screen.getByLabelText(/6-digit code/i), "654321")
        await user.click(screen.getByRole("button", { name: /^verify$/i }))

        await waitFor(() => {
            expect(mfaApi.verifyTotp).toHaveBeenCalledWith("ticket-v2", "654321")
        })
    })

    it("recovery code verify calls verifyRecoveryCode", async () => {
        axiosPostMock.mockResolvedValue(
            loginChallenge({
                status: "mfa_required",
                mfa_ticket: "ticket-v3",
                methods: ["totp", "recovery_code"],
                setup_methods: [],
                role: "STAFF",
                email: "bob@clinic.test",
            }),
        )
        ;(mfaApi.verifyRecoveryCode as ReturnType<typeof vi.fn>).mockResolvedValue({
            status: "authenticated",
            access_token: "jwt-4",
            token_type: "bearer",
            recovery_codes: null,
        })
        apiGet.mockResolvedValue({
            data: {
                id: "u1",
                email: "bob@clinic.test",
                role: "STAFF",
                institution_id: "inst-1",
                location_id: "loc-1",
                is_active: true,
            },
        })

        renderLogin()
        const user = await fillCredentialsAndSubmit("bob@clinic.test")
        await waitFor(() => {
            expect(screen.getByText(/Two-factor verification/i)).toBeInTheDocument()
        })
        await user.click(screen.getByRole("button", { name: /Use a recovery code instead/i }))
        await user.type(screen.getByLabelText(/recovery code/i), "abcd-efgh-ijkl")
        await user.click(screen.getByRole("button", { name: /verify recovery code/i }))

        await waitFor(() => {
            expect(mfaApi.verifyRecoveryCode).toHaveBeenCalledWith("ticket-v3", "abcd-efgh-ijkl")
        })
    })
})
