import { useCallback, useEffect, useState } from "react"
import {
    DollarSign,
    Loader2,
    Mail,
    Phone,
    Save,
    Edit,
    Plus,
    Trash2,
} from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    getBillingEmail,
    getROIConfig,
    listInstitutionPortalLocations,
    updateBillingEmail,
    updateROIConfig,
    updateLocationTransferNumber,
    type ROIConfig,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"

interface TransferRow {
    id: string
    locationId: string
    locationName: string
    transferNumber: string
    department: string
}

function normalizePhoneNumber(phone: string): string {
    const digits = phone.replace(/\D/g, "")
    if (digits.length === 10) {
        return `+1${digits}`
    }
    if (digits.length === 11 && digits.startsWith("1")) {
        return `+${digits}`
    }
    if (digits.length > 0) {
        return `+${digits}`
    }
    return phone
}

function formatPhoneDisplay(phone: string | null | undefined): string {
    if (!phone) return "Not configured"
    const digits = phone.replace(/\D/g, "")
    if (digits.length === 11 && digits.startsWith("1")) {
        const num = digits.slice(1)
        return `+1 (${num.slice(0, 3)}) ${num.slice(3, 6)}-${num.slice(6)}`
    }
    if (digits.length === 10) {
        return `+1 (${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`
    }
    return phone
}

function isValidPhoneNumber(phone: string): boolean {
    const digits = phone.replace(/\D/g, "")
    return digits.length >= 10 && digits.length <= 11
}

function generateId(): string {
    return Math.random().toString(36).substring(2, 9)
}

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

    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [locationsLoading, setLocationsLoading] = useState(false)

    const [transferRows, setTransferRows] = useState<TransferRow[]>([
        { id: generateId(), locationId: "", locationName: "", transferNumber: "", department: "" }
    ])
    const [transferSaving, setTransferSaving] = useState(false)

    const [editModalOpen, setEditModalOpen] = useState(false)
    const [editingLocation, setEditingLocation] = useState<InstitutionPortalLocation | null>(null)
    const [editTransferNumber, setEditTransferNumber] = useState("")
    const [editDepartment, setEditDepartment] = useState("")
    const [editTransferNumberError, setEditTransferNumberError] = useState("")

    const loadData = useCallback(async () => {
        setLoading(true)
        try {
            const [existingConfig, billingData] = await Promise.all([
                getROIConfig(),
                getBillingEmail(),
            ])
            if (existingConfig) {
                setRoiDraft(existingConfig)
            }
            if (billingData?.billing_email) {
                setBillingEmail(billingData.billing_email)
            }
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load settings")
        } finally {
            setLoading(false)
        }
    }, [])

    const loadLocations = useCallback(async () => {
        setLocationsLoading(true)
        try {
            const locs = await listInstitutionPortalLocations()
            console.log("Locations loaded:", locs)
            setLocations(locs)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load locations")
        } finally {
            setLocationsLoading(false)
        }
    }, [])

    useEffect(() => {
        void loadData()
    }, [loadData])

    useEffect(() => {
        void loadLocations()
    }, [loadLocations])

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
            await updateBillingEmail(billingEmail)
            toast.success("Billing email saved")
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save billing email")
        } finally {
            setBillingSaving(false)
        }
    }

    function handleAddRow() {
        setTransferRows([
            ...transferRows,
            { id: generateId(), locationId: "", locationName: "", transferNumber: "", department: "" }
        ])
    }

    function handleRemoveRow(id: string) {
        if (transferRows.length === 1) {
            toast.error("At least one row is required")
            return
        }
        setTransferRows(transferRows.filter((row) => row.id !== id))
    }

    function handleLocationChange(rowId: string, locationId: string) {
        const location = locations.find((loc) => loc.id === locationId)
        setTransferRows(transferRows.map((row) => 
            row.id === rowId 
                ? { ...row, locationId, locationName: location?.name || "" }
                : row
        ))
    }

    function handleTransferNumberChange(rowId: string, value: string) {
        setTransferRows(transferRows.map((row) => 
            row.id === rowId ? { ...row, transferNumber: value } : row
        ))
    }

    function handleDepartmentChange(rowId: string, value: string) {
        setTransferRows(transferRows.map((row) => 
            row.id === rowId ? { ...row, department: value } : row
        ))
    }

    async function handleSaveAllTransferNumbers() {
        const validRows = transferRows.filter((row) => row.locationId && row.transferNumber)
        
        if (validRows.length === 0) {
            toast.error("Please add at least one valid entry (location and number required)")
            return
        }

        const invalidRows = validRows.filter((row) => row.transferNumber && !isValidPhoneNumber(row.transferNumber))
        if (invalidRows.length > 0) {
            toast.error("Please enter valid phone numbers")
            return
        }

        setTransferSaving(true)
        try {
            for (const row of validRows) {
                const location = locations.find((loc) => loc.id === row.locationId)
                if (location) {
                    const normalizedNumber = normalizePhoneNumber(row.transferNumber)
                    await updateLocationTransferNumber(location.slug, normalizedNumber)
                }
            }
            
            toast.success(`${validRows.length} transfer number(s) saved`)
            setTransferRows([{ id: generateId(), locationId: "", locationName: "", transferNumber: "", department: "" }])
            void loadLocations()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save transfer numbers")
        } finally {
            setTransferSaving(false)
        }
    }

    function handleEditClick(location: InstitutionPortalLocation) {
        setEditingLocation(location)
        setEditTransferNumber(location.transfer_number || "")
        setEditDepartment((location as any).department || "")
        setEditTransferNumberError("")
        setEditModalOpen(true)
    }

    async function handleSaveEditTransferNumber() {
        if (!editingLocation) return

        if (editTransferNumber && !isValidPhoneNumber(editTransferNumber)) {
            setEditTransferNumberError("Please enter a valid phone number")
            return
        }

        setTransferSaving(true)
        try {
            const normalizedNumber = editTransferNumber ? normalizePhoneNumber(editTransferNumber) : ""
            await updateLocationTransferNumber(editingLocation.slug, normalizedNumber)

            toast.success("Transfer number updated")
            setEditModalOpen(false)
            void loadLocations()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to update transfer number")
        } finally {
            setTransferSaving(false)
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

            {/* ROI Configuration */}
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

            {/* Transfer Numbers Section */}
            <div className="space-y-4 mt-4">
                <Card className="border-primary/20 bg-background/60 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Phone className="h-4 w-4" />
                            Add Transfer Numbers
                        </CardTitle>
                        <CardDescription>
                            Configure where calls transfer to for each location during business hours. Add multiple entries and save all at once.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4 overflow-visible">
                        {/* Header Row */}
                        <div className="grid gap-4 sm:grid-cols-12 font-medium text-sm text-muted-foreground pb-2 border-b">
                            <div className="sm:col-span-4">Location</div>
                            <div className="sm:col-span-3">Transfer Number</div>
                            <div className="sm:col-span-4">Department</div>
                            <div className="sm:col-span-1"></div>
                        </div>

                        {/* Data Rows */}
                        {transferRows.map((row) => (
                            <div key={row.id} className="grid gap-4 sm:grid-cols-12 items-start">
                                <div className="sm:col-span-4">
                                    <select
                                        className="flex h-9 w-full items-center justify-between whitespace-nowrap rounded-md border border-border/80 bg-background px-3 py-2 text-sm shadow-sm ring-offset-background placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none focus:ring-2 focus:ring-primary/25 disabled:cursor-not-allowed disabled:opacity-50"
                                        value={row.locationId}
                                        onChange={(e) => handleLocationChange(row.id, e.target.value)}
                                    >
                                        <option value="">Select location</option>
                                        {locations.map((loc) => (
                                            <option key={loc.id} value={loc.id}>
                                                {loc.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                                <div className="sm:col-span-3">
                                    <Input
                                        type="tel"
                                        placeholder="+1 (555) 123-4567"
                                        value={row.transferNumber}
                                        onChange={(e) => handleTransferNumberChange(row.id, e.target.value)}
                                    />
                                </div>
                                <div className="sm:col-span-4">
                                    <Input
                                        placeholder="e.g. Reception, Billing"
                                        value={row.department}
                                        onChange={(e) => handleDepartmentChange(row.id, e.target.value)}
                                    />
                                </div>
                                <div className="sm:col-span-1">
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => handleRemoveRow(row.id)}
                                        disabled={transferRows.length === 1}
                                    >
                                        <Trash2 className="h-4 w-4 text-muted-foreground hover:text-red-500" />
                                    </Button>
                                </div>
                            </div>
                        ))}

                        {/* Action Buttons */}
                        <div className="flex gap-2 pt-2 justify-end">
                            <Button variant="outline" onClick={handleAddRow}>
                                <Plus className="h-4 w-4 mr-2" />
                                New Row
                            </Button>
                            <Button onClick={handleSaveAllTransferNumbers} disabled={transferSaving}>
                                {transferSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                Save Transfer Numbers
                            </Button>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-primary/20 bg-background/60 shadow-sm">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Phone className="h-4 w-4" />
                            Configured Transfer Numbers
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        {locationsLoading ? (
                            <div className="flex items-center justify-center py-8">
                                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                            </div>
                        ) : locations.length === 0 ? (
                            <p className="text-muted-foreground text-center py-8">No locations found</p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Location</TableHead>
                                        <TableHead>Transfer Number</TableHead>
                                        <TableHead>Department</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {locations.map((loc) => (
                                        <TableRow key={loc.id}>
                                            <TableCell className="font-medium">{loc.name}</TableCell>
                                            <TableCell>{formatPhoneDisplay(loc.transfer_number)}</TableCell>
                                            <TableCell>{(loc as any).department || "—"}</TableCell>
                                            <TableCell className="text-right">
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={() => handleEditClick(loc)}
                                                >
                                                    <Edit className="h-4 w-4 mr-1" />
                                                    Edit
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* Edit Modal */}
            <Dialog open={editModalOpen} onOpenChange={setEditModalOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Edit Transfer Number</DialogTitle>
                        <DialogDescription>
                            Update the transfer number for {editingLocation?.name}
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label htmlFor="edit-location">Location</Label>
                            <Input
                                id="edit-location"
                                value={editingLocation?.name || ""}
                                disabled
                                className="bg-muted"
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="edit-transfer-number">Transfer Number</Label>
                            <Input
                                id="edit-transfer-number"
                                type="tel"
                                placeholder="+1 (555) 123-4567"
                                value={editTransferNumber}
                                onChange={(e) => {
                                    setEditTransferNumber(e.target.value)
                                    setEditTransferNumberError("")
                                }}
                                className={editTransferNumberError ? "border-red-500" : ""}
                            />
                            {editTransferNumberError && (
                                <p className="text-sm text-red-500">{editTransferNumberError}</p>
                            )}
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="edit-department">Department</Label>
                            <Input
                                id="edit-department"
                                placeholder="e.g. Reception, Billing"
                                value={editDepartment}
                                onChange={(e) => setEditDepartment(e.target.value)}
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setEditModalOpen(false)}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleSaveEditTransferNumber}
                            disabled={transferSaving || (!!editTransferNumber && !isValidPhoneNumber(editTransferNumber))}
                        >
                            {transferSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            Save Changes
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    )
}
