import { useCallback, useEffect, useMemo, useState } from "react"
import { DollarSign, Loader2, RotateCcw, Save } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/PageHeader"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
    calculateLocationROI,
    clearLocationROIConfig,
    getLocationROIConfig,
    listInstitutionPortalLocations,
    updateLocationROIConfig,
    type InstitutionPortalLocation,
    type LocationROICalculation,
    type LocationROIConfigInput,
} from "@/lib/institution-portal-api"

const EMPTY_DRAFT: LocationROIConfigInput = {
    avg_appointment_value: 0,
    avg_new_patient_value: 0,
    staff_hourly_rate: 0,
    avg_call_duration_minutes: 4,
}

const FIELDS: { id: keyof LocationROIConfigInput; label: string; step: string }[] = [
    { id: "avg_appointment_value", label: "Avg Revenue per Appointment ($)", step: "1" },
    { id: "avg_new_patient_value", label: "Avg New Patient Value ($)", step: "1" },
    { id: "staff_hourly_rate", label: "Staff Hourly Rate ($)", step: "0.5" },
    { id: "avg_call_duration_minutes", label: "Avg Manual Call Duration (min)", step: "0.5" },
]

const money = (value: number) =>
    value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 })

function errorMessage(err: unknown, fallback: string) {
    const error = err as { response?: { data?: { detail?: string } } }
    return error?.response?.data?.detail || fallback
}

export default function LocationSettings() {
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [slug, setSlug] = useState<string>("")
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [resetting, setResetting] = useState(false)
    const [draft, setDraft] = useState<LocationROIConfigInput>(EMPTY_DRAFT)
    const [source, setSource] = useState<"location" | "institution" | null>(null)
    const [calculation, setCalculation] = useState<LocationROICalculation | null>(null)

    const selected = useMemo(
        () => locations.find((location) => location.slug === slug) ?? null,
        [locations, slug],
    )

    useEffect(() => {
        void (async () => {
            try {
                const rows = await listInstitutionPortalLocations()
                setLocations(rows)
                setSlug((current) => current || rows[0]?.slug || "")
            } catch (err: unknown) {
                toast.error(errorMessage(err, "Failed to load locations"))
            } finally {
                setLoading(false)
            }
        })()
    }, [])

    const loadForSlug = useCallback(async (locationSlug: string) => {
        setCalculation(null)
        try {
            const config = await getLocationROIConfig(locationSlug)
            if (config) {
                setSource(config.source)
                setDraft({
                    avg_appointment_value: config.avg_appointment_value,
                    avg_new_patient_value: config.avg_new_patient_value,
                    staff_hourly_rate: config.staff_hourly_rate,
                    avg_call_duration_minutes: config.avg_call_duration_minutes,
                })
            } else {
                setSource(null)
                setDraft(EMPTY_DRAFT)
            }
        } catch (err: unknown) {
            toast.error(errorMessage(err, "Failed to load location settings"))
            return
        }
        // A location with no numbers anywhere has nothing to calculate, and the
        // API says so with a 400. Surfacing that as a toast on page load would
        // read as breakage rather than "not set up yet", so leave it empty.
        try {
            setCalculation(await calculateLocationROI(locationSlug))
        } catch {
            setCalculation(null)
        }
    }, [])

    useEffect(() => {
        if (slug) void loadForSlug(slug)
    }, [slug, loadForSlug])

    async function handleSave() {
        if (!slug) return
        setSaving(true)
        try {
            const saved = await updateLocationROIConfig(slug, draft)
            setSource(saved.source)
            toast.success("Location settings saved")
            await loadForSlug(slug)
        } catch (err: unknown) {
            toast.error(errorMessage(err, "Failed to save settings"))
        } finally {
            setSaving(false)
        }
    }

    async function handleReset() {
        if (!slug) return
        setResetting(true)
        try {
            await clearLocationROIConfig(slug)
            toast.success("Reverted to institution defaults")
            await loadForSlug(slug)
        } catch (err: unknown) {
            toast.error(errorMessage(err, "Failed to reset settings"))
        } finally {
            setResetting(false)
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center py-16">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        )
    }

    return (
        <div className="space-y-4">
            <PageHeader
                title="Location Settings"
                description="Financials used to value this clinic's AI call handling."
            />

            {locations.length > 1 && (
                <div className="max-w-xs space-y-1">
                    <Label htmlFor="location-picker">Location</Label>
                    <Select value={slug} onValueChange={setSlug}>
                        <SelectTrigger id="location-picker">
                            <SelectValue placeholder="Choose a location" />
                        </SelectTrigger>
                        <SelectContent>
                            {locations.map((location) => (
                                <SelectItem key={location.id} value={location.slug}>
                                    {location.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            )}

            <div className="rounded-lg border border-border bg-background/60 shadow-sm">
                <CardHeader>
                    <CardTitle className="flex flex-wrap items-center gap-2">
                        <DollarSign className="h-4 w-4" />
                        Value Inputs
                        {source === "institution" && (
                            <Badge variant="outline">Using institution defaults</Badge>
                        )}
                        {source === "location" && <Badge variant="secondary">Set for this location</Badge>}
                        {source === null && <Badge variant="outline">Not set</Badge>}
                    </CardTitle>
                    <CardDescription>
                        {source === "institution"
                            ? `These are ${selected?.name ?? "this location"}'s inherited institution-wide figures. Saving replaces them with numbers for this clinic only.`
                            : "Your monthly subscription is billed once for the institution, so it is not entered here — it is shared across locations by call volume."}
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-2">
                        {FIELDS.map((field) => (
                            <div key={field.id} className="space-y-1">
                                <Label htmlFor={`roi-${field.id}`}>{field.label}</Label>
                                <Input
                                    id={`roi-${field.id}`}
                                    type="number"
                                    min="0"
                                    step={field.step}
                                    value={draft[field.id] || ""}
                                    onChange={(event) =>
                                        setDraft((current) => ({
                                            ...current,
                                            [field.id]: Number(event.target.value),
                                        }))
                                    }
                                />
                            </div>
                        ))}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Button onClick={handleSave} disabled={saving || !slug}>
                            {saving ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <Save className="mr-2 h-4 w-4" />
                            )}
                            Save
                        </Button>
                        {source === "location" && (
                            <Button variant="outline" onClick={handleReset} disabled={resetting}>
                                {resetting ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <RotateCcw className="mr-2 h-4 w-4" />
                                )}
                                Use institution defaults
                            </Button>
                        )}
                    </div>
                </CardContent>
            </div>

            {calculation && (
                <Card>
                    <CardHeader>
                        <CardTitle>This Month</CardTitle>
                        <CardDescription>
                            {calculation.total_calls_month} calls · {calculation.appointments_booked_month} booked ·{" "}
                            {calculation.new_patients_month} new patients
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                            <Stat label="Revenue generated" value={money(calculation.total_revenue_generated)} />
                            <Stat label="Staff cost saved" value={money(calculation.staff_cost_saved)} />
                            <Stat label="Total value" value={money(calculation.total_value)} />
                            <Stat label="Net value" value={money(calculation.net_value)} />
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Subscription apportioned to this location: {money(calculation.monthly_cost_allocated)} —{" "}
                            {calculation.cost_allocation_basis}.
                        </p>
                    </CardContent>
                </Card>
            )}
        </div>
    )
}

function Stat({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-lg border border-border bg-background/60 p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
        </div>
    )
}
