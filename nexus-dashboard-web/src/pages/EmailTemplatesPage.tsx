/**
 * Email templates — staff notifications and patient campaigns on one page.
 *
 * These were two sidebar entries whose names differed by the word "Campaign",
 * which is not enough to tell anyone which one they want. They are one page with
 * the distinction that actually matters made explicit: who receives the email.
 *
 * The two halves stay separate underneath. A staff template is bound to a
 * notification type and fires automatically; a campaign template is picked by
 * hand in a Send Email step. Merging the data would be wrong — only the
 * navigation was.
 */
import { useSearchParams } from "react-router-dom"

import { PageHeader } from "@/components/PageHeader"
import { useAuth } from "@/context/AuthContext"
import { Mail } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import StaffEmailTemplatesPanel from "@/pages/EmailTemplates"
import CampaignEmailTemplatesPanel from "@/pages/CampaignEmailTemplates"

type TemplateTab = "staff" | "campaign"

const TABS: TemplateTab[] = ["staff", "campaign"]

export default function EmailTemplatesPage() {
    const { user } = useAuth()
    // Staff templates were an institution-admin page and stay one. A super admin
    // administers any practice's campaign templates, so they get that half only
    // — merging the pages must not widen who can see what.
    const canSeeStaffTemplates = user?.role === "INSTITUTION_ADMIN"

    // Kept in the URL so either half can be linked to directly, and so the
    // retired campaign-email-templates path can land on the right tab.
    const [params, setParams] = useSearchParams()
    const requested = params.get("tab")
    const tab: TemplateTab = !canSeeStaffTemplates
        ? "campaign"
        : TABS.includes(requested as TemplateTab)
          ? (requested as TemplateTab)
          : "staff"

    return (
        <div className="space-y-6">
            <PageHeader
                art="emailTemplates"
                icon={Mail}
                title="Email templates"
                description="The emails this system sends, grouped by who receives them."
            />

            <Tabs
                value={tab}
                onValueChange={(value) => {
                    const next = new URLSearchParams(params)
                    next.set("tab", value)
                    setParams(next, { replace: true })
                }}
            >
                <TabsList>
                    {canSeeStaffTemplates && (
                        <TabsTrigger value="staff">To your team</TabsTrigger>
                    )}
                    <TabsTrigger value="campaign">To patients</TabsTrigger>
                </TabsList>

                {canSeeStaffTemplates && (
                <TabsContent value="staff" className="mt-6 space-y-4">
                    <p className="text-sm text-muted-foreground">
                        Notification emails sent to staff automatically — call summaries, urgent
                        alerts, appointment alerts. Each is bound to a notification type. Whether
                        they are sent at all is controlled under{" "}
                        <span className="font-medium text-foreground">Email preferences</span>.
                    </p>
                    <StaffEmailTemplatesPanel />
                </TabsContent>
                )}

                <TabsContent value="campaign" className="mt-6 space-y-4">
                    <p className="text-sm text-muted-foreground">
                        Reusable emails sent to patients. You choose one by hand in any{" "}
                        <span className="font-medium text-foreground">Send Email</span> step of a
                        campaign; nothing here sends on its own.
                    </p>
                    <CampaignEmailTemplatesPanel />
                </TabsContent>
            </Tabs>
        </div>
    )
}
