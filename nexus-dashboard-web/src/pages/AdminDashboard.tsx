import { useEffect, useState, useCallback } from "react"
import { Link } from "react-router-dom"
import {
    Building2,
    CheckCircle2,
    XCircle,
    Settings,
    Users,
    ArrowRight,
    Plus,
    LayoutDashboard,
} from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/foundation/compat/card"
import { Button } from "@/components/foundation/compat/button"
import { Badge } from "@/components/foundation/compat/badge"
import { Skeleton } from "@/components/foundation/compat/skeleton"
import { toast } from "sonner"
import type { InstitutionDetail } from "@/types"
import { listInstitutionsDetailed } from "@/lib/admin-api"

export default function AdminDashboard() {
    const [institutions, setInstitutions] = useState<InstitutionDetail[]>([])
    const [loading, setLoading] = useState(true)

    const fetchInstitutions = useCallback(async () => {
        try {
            const data = await listInstitutionsDetailed()
            setInstitutions(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load institutions"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchInstitutions()
    }, [fetchInstitutions])

    const activeInstitutions = institutions.filter((t) => t.is_active)
    const inactiveInstitutions = institutions.filter((t) => !t.is_active)
    const fullyConfigured = institutions.filter(
        (t) =>
            t.is_active &&
            (
                t.pms_type === "gotracker"
                    ? t.has_gotracker_key
                    : (t.has_nexhealth_key || t.has_system_nexhealth_key)
            ) &&
            t.has_retell_secret
    )

    const integrationCounts = {
        nexhealth: institutions.filter((t) => t.has_nexhealth_key || t.has_system_nexhealth_key).length,
        gotracker: institutions.filter((t) => t.has_gotracker_key).length,
        retell: institutions.filter((t) => t.has_retell_secret).length,
    }

    const adminCards = [
        { label: "Total Institutions", value: institutions.length, icon: Building2, description: "All registered practices" },
        { label: "Active", value: activeInstitutions.length, icon: CheckCircle2, description: "Currently active" },
        { label: "Inactive", value: inactiveInstitutions.length, icon: XCircle, description: "Disabled or paused" },
        { label: "Fully Configured", value: fullyConfigured.length, icon: Settings, description: "PMS + Retell ready" },
    ]

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={LayoutDashboard}
                title="Admin Dashboard"
                description="Platform overview and institution management."
                actions={
                    <Link to="/institutions">
                        <Button variant="outline" className="gap-2">
                            <Plus className="h-4 w-4" />
                            Add Institution
                        </Button>
                    </Link>
                }
            />

            {/* Stats Cards */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                {adminCards.map((card) => (
                    <div key={card.label} className="rounded-xl border border-border/80 bg-card p-5 shadow-sm">
                            <div className="mb-3 flex items-center justify-between">
                                <span className="text-xs font-medium text-muted-foreground">{card.label}</span>
                                <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted">
                                    <card.icon className="h-4 w-4 text-foreground" />
                                </div>
                            </div>
                            {loading ? (
                                <Skeleton className="h-12 w-20" />
                            ) : (
                                <>
                                    <div className="text-2xl font-semibold tabular-nums tracking-tight text-foreground">
                                        {card.value}
                                    </div>
                                    <p className="mt-1 text-xs text-muted-foreground">
                                        {card.description}
                                    </p>
                                </>
                            )}
                    </div>
                ))}
            </div>

            {/* Integration Overview */}
            {!loading && (
                <Card>
                    <CardHeader>
                        <CardTitle>Integration Coverage</CardTitle>
                        <CardDescription>
                            Number of institutions with each integration configured.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap gap-3">
                            <Badge variant="secondary" className="text-sm px-3 py-1 border border-border bg-primary/10 text-primary">
                                NexHealth: {integrationCounts.nexhealth}
                            </Badge>
                            <Badge variant="secondary" className="text-sm px-3 py-1 border border-border bg-primary2/10 text-primary2">
                                GoTracker: {integrationCounts.gotracker}
                            </Badge>
                            <Badge variant="secondary" className="text-sm px-3 py-1 border border-accent-foreground/20 bg-accent text-accent-foreground">
                                Retell AI: {integrationCounts.retell}
                            </Badge>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Institution Table */}
            <Card>
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle>Institutions</CardTitle>
                            <CardDescription>
                                All registered practices on the platform.
                            </CardDescription>
                        </div>
                        <Link to="/institutions">
                            <Button variant="ghost" size="sm" className="gap-1">
                                View All
                                <ArrowRight className="h-3 w-3" />
                            </Button>
                        </Link>
                    </div>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="space-y-3">
                            {Array.from({ length: 5 }).map((_, i) => (
                                <Skeleton key={i} className="h-12 w-full" />
                            ))}
                        </div>
                    ) : institutions.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                            <Users className="h-10 w-10 mb-2" />
                            <p>No institutions yet.</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-border text-left text-muted-foreground">
                                        <th className="pb-3 font-medium">Name</th>
                                        <th className="pb-3 font-medium">Contact</th>
                                        <th className="pb-3 font-medium">Status</th>
                                        <th className="pb-3 font-medium">PMS</th>
                                        <th className="pb-3 font-medium">Retell</th>
                                        <th className="pb-3 font-medium sr-only">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {institutions.map((inst) => (
                                        <tr key={inst.id} className="border-b border-border/60 last:border-0 hover:bg-muted/40 transition-colors">
                                            <td className="py-3 font-medium">{inst.name}</td>
                                            <td className="py-3 text-muted-foreground text-xs">
                                                {inst.user?.email ?? "—"}
                                            </td>
                                            <td className="py-3">
                                                <Badge
                                                    variant="secondary"
                                                    className={inst.is_active
                                                        ? "border border-border bg-primary/10 text-primary"
                                                        : "border border-border bg-muted text-muted-foreground"}
                                                >
                                                    {inst.is_active ? "Active" : "Inactive"}
                                                </Badge>
                                            </td>
                                            <td className="py-3">
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${
                                                    inst.pms_type === "gotracker"
                                                        ? inst.has_gotracker_key ? "bg-green-500" : "bg-gray-300"
                                                        : inst.has_nexhealth_key || inst.has_system_nexhealth_key ? "bg-green-500" : "bg-gray-300"
                                                }`} />
                                            </td>
                                            <td className="py-3">
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${inst.has_retell_secret ? "bg-green-500" : "bg-gray-300"}`} />
                                            </td>
                                            <td className="py-3 text-right">
                                                <Link to={`/institutions/${inst.slug}`}>
                                                    <Button variant="ghost" size="sm">
                                                        View
                                                    </Button>
                                                </Link>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    )
}
