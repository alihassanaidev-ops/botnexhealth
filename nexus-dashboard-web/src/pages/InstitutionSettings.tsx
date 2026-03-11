import { useCallback, useEffect, useState } from "react"
import {
    DollarSign,
    Loader2,
    Mail,
    Save,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    getROIConfig,
    updateROIConfig,
    type ROIConfig,
} from "@/lib/institution-portal-api"

export default function InstitutionSettings() {
    const [loading, setLoading] = useState(true)
    const [roiDraft, setRoiDraft] = useState<ROIConfig>({
        avg_appointment_value: 0,
        avg_new_patient_value: 0,
        monthly_subscription_cost: 0,
        staff_hourly_rate: 0,
        avg_call_duration_minutes: 0,
    })
    const [roiSaving, setRoiSaving] = useState(false)
    const [billingEmail, setBillingEmail] = useState("")
    const [billingSaving, setBillingSaving] = useState(false)

    const loadData = useCallback(async () => {
        setLoading(true)
        try {
            const existingConfig = await getROIConfig()
            if (existingConfig) {
                setRoiDraft(existingConfig)
            }
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load ROI configuration")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        void loadData()
    }, [loadData])

    async function handleSaveROIConfig() {
        setRoiSaving(true)
        try {
            await updateROIConfig(roiDraft)
            toast.success("ROI configuration saved")
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save ROI configuration")
        } finally {
            setRoiSaving(false)
        }
    }

    async function handleSaveBillingEmail() {
        setBillingSaving(true)
        try {
            // TODO: Replace with actual API call when backend endpoint is ready
            // await updateBillingEmail(billingEmail)
            await new Promise((resolve) => setTimeout(resolve, 500)) // Fake delay
            toast.success("Billing email saved")
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save billing email")
        } finally {
            setBillingSaving(false)
        }
    }

    if (loading) {
        return (
            <div className="flex-1 space-y-4 bg-gradient-to-b from-background via-background to-accent/20 p-8 pt-6">
                <div className="flex items-center justify-center min-h-[400px]">
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                </div>
            </div>
        )
    }

    return (
        <div className="flex-1 space-y-4 bg-gradient-to-b from-background via-background to-accent/20 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
                    <p className="text-muted-foreground">
                        Configure your institution settings and preferences.
                    </p>
                </div>
            </div>

            {/* Billing Section */}
            <Card className="border-primary/20 bg-background/60 shadow-sm mt-4">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Mail className="h-4 w-4" />
                        Billing
                    </CardTitle>
                    <CardDescription>
                        Email address for receiving invoices from ScaleNexusAI.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="max-w-md">
                        <Label htmlFor="billing-email">Billing Email</Label>
                        <Input
                            id="billing-email"
                            type="email"
                            value={billingEmail}
                            onChange={(e) => setBillingEmail(e.target.value)}
                            placeholder="billing@clinic.com"
                            className="mt-1"
                        />
                    </div>
                    <Button onClick={handleSaveBillingEmail} disabled={billingSaving}>
                        {billingSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        Save
                    </Button>
                </CardContent>
            </Card>

            <div className="rounded-lg border border-primary/20 bg-background/60 shadow-sm mt-4">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <DollarSign className="h-4 w-4" />
                        ROI Configuration
                    </CardTitle>
                    <CardDescription>
                        Enter your practice financials to calculate the return on investment from AI call handling.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        <div className="space-y-1">
                            <Label htmlFor="roi-appt-value">Avg Revenue per Appointment ($)</Label>
                            <Input
                                id="roi-appt-value"
                                type="number"
                                min={0}
                                step={1}
                                value={roiDraft.avg_appointment_value || ""}
                                onChange={(e) => setRoiDraft((d) => ({ ...d, avg_appointment_value: Number(e.target.value) }))}
                                placeholder="e.g. 200"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="roi-new-patient-value">Avg New Patient Value ($)</Label>
                            <Input
                                id="roi-new-patient-value"
                                type="number"
                                min={0}
                                step={1}
                                value={roiDraft.avg_new_patient_value || ""}
                                onChange={(e) => setRoiDraft((d) => ({ ...d, avg_new_patient_value: Number(e.target.value) }))}
                                placeholder="e.g. 300"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="roi-subscription">Monthly Subscription ($)</Label>
                            <Input
                                id="roi-subscription"
                                type="number"
                                min={0}
                                step={1}
                                value={roiDraft.monthly_subscription_cost || ""}
                                onChange={(e) => setRoiDraft((d) => ({ ...d, monthly_subscription_cost: Number(e.target.value) }))}
                                placeholder="e.g. 500"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="roi-staff-rate">Staff Hourly Rate ($)</Label>
                            <Input
                                id="roi-staff-rate"
                                type="number"
                                min={0}
                                step={0.5}
                                value={roiDraft.staff_hourly_rate || ""}
                                onChange={(e) => setRoiDraft((d) => ({ ...d, staff_hourly_rate: Number(e.target.value) }))}
                                placeholder="e.g. 18"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="roi-call-duration">Avg Manual Call Duration (min)</Label>
                            <Input
                                id="roi-call-duration"
                                type="number"
                                min={0}
                                step={0.5}
                                value={roiDraft.avg_call_duration_minutes || ""}
                                onChange={(e) => setRoiDraft((d) => ({ ...d, avg_call_duration_minutes: Number(e.target.value) }))}
                                placeholder="e.g. 4"
                            />
                        </div>
                    </div>
                    <Button onClick={handleSaveROIConfig} disabled={roiSaving}>
                        {roiSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        Save Configuration
                    </Button>
                </CardContent>
            </div>
        </div>
    )
}
