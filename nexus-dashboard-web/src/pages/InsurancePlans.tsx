import { useEffect, useState, useCallback } from "react"
import { Loader2, Pencil, Plus, RefreshCcw, Shield, Trash2, X } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { useAuth } from "@/context/AuthContext"
import {
    createInsurancePlan,
    deleteInsurancePlan,
    listInsurancePlans,
    listInstitutionPortalLocations,
    updateInsurancePlan,
    type InsurancePlan,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"

export default function InsurancePlans() {
    const { user } = useAuth()
    const [loading, setLoading] = useState(true)
    const [locations, setLocations] = useState<InstitutionPortalLocation[]>([])
    const [selectedSlug, setSelectedSlug] = useState("")
    const [plans, setPlans] = useState<InsurancePlan[]>([])
    const [saving, setSaving] = useState(false)
    const [deletingId, setDeletingId] = useState<string | null>(null)

    // Form state
    const [showForm, setShowForm] = useState(false)
    const [editingPlan, setEditingPlan] = useState<InsurancePlan | null>(null)
    const [formName, setFormName] = useState("")
    const [formDescription, setFormDescription] = useState("")

    const canManage = user?.role === "INSTITUTION_ADMIN" || user?.role === "LOCATION_ADMIN"

    useEffect(() => {
        async function loadLocations() {
            try {
                const locs = await listInstitutionPortalLocations()
                setLocations(locs)
                if (locs.length > 0) {
                    setSelectedSlug(locs[0].slug)
                }
            } catch (err: unknown) {
                const error = err as { response?: { data?: { detail?: string } } };
                toast.error(error?.response?.data?.detail || "Failed to load locations")
            }
        }
        void loadLocations()
    }, [])

    const loadPlans = useCallback(async () => {
        if (!selectedSlug) return
        setLoading(true)
        try {
            const data = await listInsurancePlans(selectedSlug)
            setPlans(data)
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to load insurance plans")
        } finally {
            setLoading(false)
        }
    }, [selectedSlug]);

    useEffect(() => {
        if (!selectedSlug) return
        void loadPlans()
    }, [selectedSlug, loadPlans])

    function openCreateForm() {
        setEditingPlan(null)
        setFormName("")
        setFormDescription("")
        setShowForm(true)
    }

    function openEditForm(plan: InsurancePlan) {
        setEditingPlan(plan)
        setFormName(plan.name)
        setFormDescription(plan.description || "")
        setShowForm(true)
    }

    function closeForm() {
        setShowForm(false)
        setEditingPlan(null)
        setFormName("")
        setFormDescription("")
    }

    async function handleSubmit() {
        if (!formName.trim() || !selectedSlug) return
        setSaving(true)
        try {
            const payload = {
                name: formName.trim(),
                description: formDescription.trim() || undefined,
            }
            if (editingPlan) {
                await updateInsurancePlan(selectedSlug, editingPlan.id, payload)
                toast.success("Insurance plan updated")
            } else {
                await createInsurancePlan(selectedSlug, payload)
                toast.success("Insurance plan added")
            }
            closeForm()
            await loadPlans()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save insurance plan")
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(plan: InsurancePlan) {
        if (!window.confirm(`Remove "${plan.name}" from accepted insurance plans?`)) return
        setDeletingId(plan.id)
        try {
            await deleteInsurancePlan(selectedSlug, plan.id)
            toast.success("Insurance plan removed")
            await loadPlans()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to remove insurance plan")
        } finally {
            setDeletingId(null)
        }
    }

    const showLocationPicker = user?.role === "INSTITUTION_ADMIN" && locations.length > 1

    return (
        <div className="space-y-6 bg-gradient-to-b from-background via-background to-accent/20">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Insurance Plans</h1>
                    <p className="mt-1 text-muted-foreground">
                        Manage accepted insurance plans for your location. The AI agent uses this list to answer caller questions.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" onClick={loadPlans} disabled={loading || !selectedSlug}>
                        {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                        Refresh
                    </Button>
                    {canManage && (
                        <Button onClick={openCreateForm} disabled={!selectedSlug}>
                            <Plus className="mr-2 h-4 w-4" />
                            Add Plan
                        </Button>
                    )}
                </div>
            </div>

            {showLocationPicker && (
                <div className="max-w-xs space-y-2">
                    <Label>Location</Label>
                    <Select value={selectedSlug} onValueChange={setSelectedSlug}>
                        <SelectTrigger>
                            <SelectValue placeholder="Select location" />
                        </SelectTrigger>
                        <SelectContent>
                            {locations.map((loc) => (
                                <SelectItem key={loc.slug} value={loc.slug}>
                                    {loc.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            )}

            {showForm && (
                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-base">
                                {editingPlan ? "Edit Insurance Plan" : "Add Insurance Plan"}
                            </CardTitle>
                            <Button variant="ghost" size="icon" onClick={closeForm}>
                                <X className="h-4 w-4" />
                            </Button>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="plan-name">Name</Label>
                            <Input
                                id="plan-name"
                                placeholder="e.g. Delta Dental PPO"
                                value={formName}
                                onChange={(e) => setFormName(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") void handleSubmit()
                                }}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="plan-description">Description (optional)</Label>
                            <Textarea
                                id="plan-description"
                                placeholder="e.g. Covers preventive and basic services"
                                value={formDescription}
                                onChange={(e) => setFormDescription(e.target.value)}
                                rows={2}
                            />
                        </div>
                        <div className="flex gap-2">
                            <Button onClick={handleSubmit} disabled={saving || !formName.trim()}>
                                {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                {editingPlan ? "Save Changes" : "Add Plan"}
                            </Button>
                            <Button variant="outline" onClick={closeForm}>
                                Cancel
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Shield className="h-4 w-4" />
                        Accepted Insurance Plans
                    </CardTitle>
                    <CardDescription>
                        {locations.find((l) => l.slug === selectedSlug)?.name || "Select a location"}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Description</TableHead>
                                {canManage && <TableHead className="text-right">Actions</TableHead>}
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {plans.map((plan) => (
                                <TableRow key={plan.id}>
                                    <TableCell className="font-medium">{plan.name}</TableCell>
                                    <TableCell className="text-muted-foreground">
                                        {plan.description || "—"}
                                    </TableCell>
                                    {canManage && (
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-2">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    onClick={() => openEditForm(plan)}
                                                >
                                                    <Pencil className="h-4 w-4" />
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    disabled={deletingId === plan.id}
                                                    onClick={() => handleDelete(plan)}
                                                >
                                                    {deletingId === plan.id ? (
                                                        <Loader2 className="h-4 w-4 animate-spin" />
                                                    ) : (
                                                        <Trash2 className="h-4 w-4 text-destructive" />
                                                    )}
                                                </Button>
                                            </div>
                                        </TableCell>
                                    )}
                                </TableRow>
                            ))}
                            {!plans.length && !loading && (
                                <TableRow>
                                    <TableCell colSpan={canManage ? 3 : 2} className="py-10 text-center text-muted-foreground">
                                        No insurance plans added yet.{" "}
                                        {canManage && "Click \"Add Plan\" to get started."}
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    )
}
