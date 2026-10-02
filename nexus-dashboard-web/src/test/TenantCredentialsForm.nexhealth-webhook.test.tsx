import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { TenantCredentialsForm } from "@/components/tenants/TenantCredentialsForm"
import api from "@/lib/api"

vi.mock("@/lib/api", () => ({
    default: {
        get: vi.fn(),
        post: vi.fn(),
        patch: vi.fn(),
    },
}))
vi.mock("@/lib/admin-api", () => ({
    getInstitutionProvisioning: vi.fn().mockResolvedValue({ configured: false }),
    updateInstitutionTwilioProvisioning: vi.fn(),
    clearInstitutionTwilioProvisioning: vi.fn(),
}))
vi.mock("sonner", () => ({
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const mockedApi = api as unknown as {
    get: ReturnType<typeof vi.fn>
    post: ReturnType<typeof vi.fn>
}

const institution = {
    id: "inst-1",
    name: "Practice",
    slug: "practice",
    is_active: true,
    pms_type: "nexhealth",
    has_nexhealth_key: false,
    nexhealth_credential_mode: "platform" as const,
    has_system_nexhealth_key: true,
    has_gotracker_key: false,
    has_retell_secret: false,
    user: null,
}

const pendingStatus = {
    callback_url: "https://api.staging.scalenexus.ai/api/v1/nexhealth/webhooks/appointments",
    callback_ready: true,
    groups: [
        {
            subdomain: "demo-practice",
            status: "pending",
            callback_url: null,
            provider_endpoint_id: null,
            provider_subscription_count: 0,
            required_events: ["appointment_created", "appointment_updated"],
            missing_events: [],
            signing_secret_configured: false,
            last_event_at: null,
            last_health_check_at: null,
            locations: [
                { location_id: "loc-1", location_name: "Downtown", nexhealth_location_id: "101" },
                { location_id: "loc-2", location_name: "Uptown", nexhealth_location_id: "102" },
            ],
        },
    ],
}

describe("NexHealth webhook super-admin control", () => {
    beforeEach(() => {
        mockedApi.get.mockReset()
        mockedApi.post.mockReset()
        mockedApi.get.mockResolvedValue({ data: pendingStatus })
        mockedApi.post.mockResolvedValue({
            data: {
                ...pendingStatus,
                groups: pendingStatus.groups.map((group) => ({
                    ...group,
                    status: "active",
                    provider_endpoint_id: "77",
                    provider_subscription_count: 2,
                    signing_secret_configured: true,
                })),
            },
        })
    })

    it("shows subdomain location routing and connects without editable webhook ids", async () => {
        const user = userEvent.setup()
        render(<TenantCredentialsForm institution={institution} onUpdated={vi.fn()} />)

        expect(await screen.findByText("NexHealth webhooks")).toBeInTheDocument()
        expect(screen.getByText("demo-practice")).toBeInTheDocument()
        expect(screen.getByText("Downtown")).toBeInTheDocument()
        expect(screen.getByText("Uptown")).toBeInTheDocument()
        expect(screen.queryByRole("textbox", { name: /endpoint/i })).not.toBeInTheDocument()

        await user.click(screen.getByRole("button", { name: "Connect" }))

        await waitFor(() => {
            expect(mockedApi.post).toHaveBeenCalledWith(
                "/admin/institutions/practice/nexhealth/webhook/connect",
            )
        })
        expect(await screen.findByText("Connected and signed")).toBeInTheDocument()
    })
})
