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
import { useAuth } from "@/context/AuthContext"
import { StatsCard } from "@/components/dashboard/StatsCard"
import type { TenantDetail } from "@/types"
import { listTenantsDetailed } from "@/lib/admin-api"

export default function AdminDashboard() {
    const { user } = useAuth()
    const [tenants, setTenants] = useState<TenantDetail[]>([])
    const [loading, setLoading] = useState(true)

    const fetchTenants = useCallback(async () => {
        try {
            const data = await listTenantsDetailed()
            setTenants(data)
        } catch (error: unknown) {
            const message = error instanceof Error ? error.message : "Failed to load tenants"
            toast.error(message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchTenants()
    }, [fetchTenants])

    const activeTenants = tenants.filter((t) => t.is_active)
    const inactiveTenants = tenants.filter((t) => !t.is_active)
    const fullyConfigured = tenants.filter(
        (t) =>
            t.is_active &&
            (t.has_nexhealth_key || t.has_system_nexhealth_key) &&
            t.has_retell_secret
    )

    const integrationCounts = {
        nexhealth: tenants.filter((t) => t.has_nexhealth_key || t.has_system_nexhealth_key).length,
        ghl: tenants.filter((t) => t.has_ghl_key).length,
        retell: tenants.filter((t) => t.has_retell_secret).length,
        sikka: tenants.filter((t) => t.has_sikka_credentials).length,
    }

    const hour = new Date().getHours()
    const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"

    return (
        <div className="flex-1 space-y-6 p-8 pt-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">
                        {greeting}{user?.email ? `, ${user.email.split("@")[0]}` : ""}
                    </h2>
                    <p className="text-muted-foreground">
                        Platform overview and tenant management.
                    </p>
                </div>
                <Link to="/tenants">
                    <Button variant="outline" className="gap-2">
                        <Plus className="h-4 w-4" />
                        Add Tenant
                    </Button>
                </Link>
            </div>

            {/* Stats Cards */}
            {loading ? (
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <Card key={i}>
                            <CardContent className="p-6 space-y-3">
                                <Skeleton className="h-4 w-24" />
                                <Skeleton className="h-8 w-16" />
                                <Skeleton className="h-3 w-32" />
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : (
                <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                    <StatsCard
                        title="Total Tenants"
                        value={String(tenants.length)}
                        description="All registered practices"
                        icon={Building2}
                    />
                    <StatsCard
                        title="Active"
                        value={String(activeTenants.length)}
                        description="Currently active tenants"
                        icon={CheckCircle2}
                    />
                    <StatsCard
                        title="Inactive"
                        value={String(inactiveTenants.length)}
                        description="Disabled or paused tenants"
                        icon={XCircle}
                    />
                    <StatsCard
                        title="Fully Configured"
                        value={String(fullyConfigured.length)}
                        description="NexHealth + Retell ready"
                        icon={Settings}
                    />
                </div>
            )}

            {/* Integration Overview */}
            {!loading && (
                <Card>
                    <CardHeader>
                        <CardTitle>Integration Coverage</CardTitle>
                        <CardDescription>
                            Number of tenants with each integration configured.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="flex flex-wrap gap-3">
                            <Badge variant="secondary" className="text-sm px-3 py-1">
                                NexHealth: {integrationCounts.nexhealth}
                            </Badge>
                            <Badge variant="secondary" className="text-sm px-3 py-1">
                                GoHighLevel: {integrationCounts.ghl}
                            </Badge>
                            <Badge variant="secondary" className="text-sm px-3 py-1">
                                Retell AI: {integrationCounts.retell}
                            </Badge>
                            <Badge variant="secondary" className="text-sm px-3 py-1">
                                Sikka: {integrationCounts.sikka}
                            </Badge>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Tenant Table */}
            <Card>
                <CardHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle>Tenants</CardTitle>
                            <CardDescription>
                                All registered practices on the platform.
                            </CardDescription>
                        </div>
                        <Link to="/tenants">
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
                    ) : tenants.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                            <Users className="h-10 w-10 mb-2" />
                            <p>No tenants yet.</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b text-left text-muted-foreground">
                                        <th className="pb-3 font-medium">Name</th>
                                        <th className="pb-3 font-medium">Contact</th>
                                        <th className="pb-3 font-medium">Status</th>
                                        <th className="pb-3 font-medium">NexHealth</th>
                                        <th className="pb-3 font-medium">GHL</th>
                                        <th className="pb-3 font-medium">Retell</th>
                                        <th className="pb-3 font-medium">Sikka</th>
                                        <th className="pb-3 font-medium sr-only">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {tenants.map((tenant) => (
                                        <tr key={tenant.id} className="border-b last:border-0">
                                            <td className="py-3 font-medium">{tenant.name}</td>
                                            <td className="py-3 text-muted-foreground text-xs">
                                                {tenant.user?.email ?? "—"}
                                            </td>
                                            <td className="py-3">
                                                <Badge variant={tenant.is_active ? "default" : "secondary"}>
                                                    {tenant.is_active ? "Active" : "Inactive"}
                                                </Badge>
                                            </td>
                                            <td className="py-3">
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${tenant.has_nexhealth_key || tenant.has_system_nexhealth_key ? "bg-green-500" : "bg-gray-300"}`} />
                                            </td>
                                            <td className="py-3">
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${tenant.has_ghl_key ? "bg-green-500" : "bg-gray-300"}`} />
                                            </td>
                                            <td className="py-3">
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${tenant.has_retell_secret ? "bg-green-500" : "bg-gray-300"}`} />
                                            </td>
                                            <td className="py-3">
                                                <span className={`inline-block h-2.5 w-2.5 rounded-full ${tenant.has_sikka_credentials ? "bg-green-500" : "bg-gray-300"}`} />
                                            </td>
                                            <td className="py-3 text-right">
                                                <Link to={`/tenants/${tenant.slug}`}>
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
