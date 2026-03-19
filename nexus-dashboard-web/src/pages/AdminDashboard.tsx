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
} from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
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
            (t.has_nexhealth_key || t.has_system_nexhealth_key) &&
            t.has_retell_secret
    )

    const integrationCounts = {
        nexhealth: institutions.filter((t) => t.has_nexhealth_key || t.has_system_nexhealth_key).length,
        retell: institutions.filter((t) => t.has_retell_secret).length,
    }

    const adminCards = [
        { label: "Total Institutions", value: institutions.length, icon: Building2, glowRgb: "139,92,246", description: "All registered practices" },
        { label: "Active", value: activeInstitutions.length, icon: CheckCircle2, glowRgb: "16,185,129", description: "Currently active" },
        { label: "Inactive", value: inactiveInstitutions.length, icon: XCircle, glowRgb: "239,68,68", description: "Disabled or paused" },
        { label: "Fully Configured", value: fullyConfigured.length, icon: Settings, glowRgb: "59,130,246", description: "NexHealth + Retell ready" },
    ]

    return (
        <div className="relative flex-1 space-y-6 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-2xl font-bold tracking-tight">Admin Dashboard</h2>
                    <p className="text-sm text-muted-foreground/70 mt-0.5">
                        Platform overview and institution management.
                    </p>
                </div>
                <Link to="/institutions">
                    <Button variant="outline" className="gap-2">
                        <Plus className="h-4 w-4" />
                        Add Institution
                    </Button>
                </Link>
            </div>

            {/* Stats Cards */}
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                {adminCards.map((card) => (
                    <div key={card.label} className="group relative overflow-hidden rounded-2xl bg-gradient-to-br from-card via-card to-accent/30 border border-border/60 shadow-sm transition-all duration-300 ease-out hover:-translate-y-1 hover:shadow-lg cursor-default">
                        <div
                            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-40 h-40 rounded-full opacity-[0.08] blur-3xl transition-opacity duration-300 group-hover:opacity-[0.15]"
                            style={{ background: `radial-gradient(circle, rgba(${card.glowRgb}, 0.8) 0%, transparent 70%)` }}
                        />
                        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent" />
                        <div className="relative p-6">
                            <div className="flex items-center justify-between mb-5">
                                <span className="text-sm font-medium text-muted-foreground">{card.label}</span>
                                <div className="rounded-xl p-2.5 bg-primary/10">
                                    <card.icon className="h-4 w-4 text-primary" />
                                </div>
                            </div>
                            {loading ? (
                                <Skeleton className="h-12 w-20" />
                            ) : (
                                <>
                                    <div className="text-5xl font-extralight tabular-nums tracking-tight text-foreground">
                                        {card.value}
                                    </div>
                                    <p className="text-xs mt-2 text-muted-foreground/60 font-medium tracking-wide uppercase">
                                        {card.description}
                                    </p>
                                </>
                            )}
                        </div>
                    </div>
                ))}
            </div>

            {/* Integration Overview */}
            {!loading && (
                <Card className="border-border bg-gradient-to-r from-secondary/70 via-accent/60 to-primary2/25">
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
                            <Badge variant="secondary" className="text-sm px-3 py-1 border border-accent-foreground/20 bg-accent text-accent-foreground">
                                Retell AI: {integrationCounts.retell}
                            </Badge>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Institution Table */}
            <Card className="border-border/80 shadow-sm">
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
                                        <th className="pb-3 font-medium">NexHealth</th>
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
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${inst.has_nexhealth_key || inst.has_system_nexhealth_key ? "bg-green-500" : "bg-gray-300"}`} />
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
