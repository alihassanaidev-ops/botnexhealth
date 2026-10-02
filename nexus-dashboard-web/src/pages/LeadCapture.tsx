/**
 * How leads reach the clinic — one page for both intake routes.
 *
 * These were two sidebar entries, "Contact forms" and "Lead forms", which named
 * the mechanism rather than the job. Both answer the same question — how does
 * someone who filled in a form become a contact — so they are one page split by
 * how much work the clinic has to do, not by which technology is involved.
 *
 * The two routes stay distinct on purpose, and the page says so: a connected
 * provider raises `form_submitted` while a posted lead raises `enquiry.received`,
 * and a workflow built on the wrong one silently never enrols anybody. Naming
 * the trigger here is what stops that being discovered the hard way.
 */
import { useSearchParams } from "react-router-dom"

import { PageHeader } from "@/components/PageHeader"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import EnquirySourcesPanel from "@/pages/EnquirySources"
import FormIntegrationsPanel from "@/pages/FormIntegrations"

type LeadTab = "connected" | "direct"

const TABS: LeadTab[] = ["connected", "direct"]

export default function LeadCapture() {
    // The tab lives in the URL so the provider OAuth callback can return
    // straight to the connected-apps tab, and so a link to either half works.
    const [params, setParams] = useSearchParams()
    const requested = params.get("tab")
    const tab: LeadTab = TABS.includes(requested as LeadTab) ? (requested as LeadTab) : "connected"

    return (
        <div className="space-y-6">
            <PageHeader
                art="leadForms"
                title="Lead capture"
                description="Every way a person can reach you from a form, in one place."
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
                    <TabsTrigger value="connected">Connected apps</TabsTrigger>
                    <TabsTrigger value="direct">Your own forms</TabsTrigger>
                </TabsList>

                <TabsContent value="connected" className="mt-6 space-y-4">
                    <p className="text-sm text-muted-foreground">
                        Connect Meta and Typeform, choose which forms bring people in, and map their
                        questions onto your contact fields. Submissions here start workflows that
                        trigger on <span className="font-medium text-foreground">Form submitted</span>.
                    </p>
                    <FormIntegrationsPanel />
                </TabsContent>

                <TabsContent value="direct" className="mt-6 space-y-4">
                    <p className="text-sm text-muted-foreground">
                        For your own website form, another form builder, or an automation tool — we
                        give you a secure address to post to. Leads that arrive here start workflows
                        that trigger on <span className="font-medium text-foreground">Enquiry received</span>.
                    </p>
                    <EnquirySourcesPanel />
                </TabsContent>
            </Tabs>
        </div>
    )
}
