import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Pencil } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from "@/components/ui/sheet";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { TenantCredentialsForm } from "@/components/tenants/TenantCredentialsForm";
import { LocationList } from "@/components/tenants/LocationList";
import { toast } from "sonner";
import api from "@/lib/api";
import type { TenantDetail as TenantDetailType } from "@/types";

const overviewSchema = z.object({
    name: z.string().min(2, "Name must be at least 2 characters"),
    is_active: z.boolean(),
});

type OverviewFormValues = z.infer<typeof overviewSchema>;

export default function TenantDetail() {
    const { slug } = useParams<{ slug: string }>();
    const navigate = useNavigate();
    const [tenant, setTenant] = useState<TenantDetailType | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [editSheetOpen, setEditSheetOpen] = useState(false);
    const [activeTab, setActiveTab] = useState("overview");

    const fetchTenant = useCallback(async () => {
        setIsLoading(true);
        try {
            const { data } = await api.get<TenantDetailType>(`/admin/tenants/${slug}`);
            setTenant(data);
        } catch (error: any) {
            console.error("Failed to fetch tenant", error);
            if (error.response?.status === 404) {
                toast.error("Tenant not found");
                navigate("/tenants");
            } else {
                toast.error("Failed to fetch tenant details");
            }
        } finally {
            setIsLoading(false);
        }
    }, [slug, navigate]);

    useEffect(() => {
        fetchTenant();
    }, [fetchTenant]);

    const form = useForm<OverviewFormValues>({
        resolver: zodResolver(overviewSchema),
        defaultValues: {
            name: "",
            is_active: true,
        },
    });

    // Reset form when tenant loads
    useEffect(() => {
        if (tenant) {
            form.reset({
                name: tenant.name,
                is_active: tenant.is_active,
            });
        }
    }, [tenant, form]);

    async function onOverviewSubmit(values: OverviewFormValues) {
        if (!tenant) return;

        const payload: Record<string, unknown> = {};
        if (values.name !== tenant.name) payload.name = values.name;
        if (values.is_active !== tenant.is_active) payload.is_active = values.is_active;

        if (Object.keys(payload).length === 0) {
            toast.info("No changes to save");
            return;
        }

        try {
            await api.patch(`/admin/tenants/${slug}`, payload);
            toast.success("Tenant updated");
            setEditSheetOpen(false);
            fetchTenant();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to update tenant");
        }
    }

    const IntegrationCard = ({
        name,
        description,
        isConfigured,
        hasSystemKey = false,
        onConfigure,
    }: {
        name: string;
        description: string;
        isConfigured: boolean;
        hasSystemKey?: boolean;
        onConfigure: () => void;
    }) => {
        let statusColor = "bg-neutral-300 dark:bg-neutral-600";
        let statusText = "Not Connected";

        if (isConfigured) {
            statusColor = "bg-green-500";
            statusText = "Connected";
        } else if (hasSystemKey) {
            statusColor = "bg-green-500/50";
            statusText = "Connected (System)";
        }

        return (
            <div className="group flex items-center justify-between rounded-lg border border-border/60 bg-card p-4 transition-all hover:border-border/80 hover:bg-muted/50 hover:shadow-sm">
                <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                        <span className="font-semibold">{name}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{description}</p>
                    <div className="mt-1.5 flex items-center gap-1.5">
                        <div
                            className={`h-1.5 w-1.5 rounded-full ${statusColor}`}
                        />
                        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                            {statusText}
                        </span>
                    </div>
                </div>
                <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-background/80 hover:text-foreground"
                    onClick={onConfigure}
                >
                    <Pencil className="h-4 w-4" />
                    <span className="sr-only">Configure {name}</span>
                </Button>
            </div>
        );
    };

    if (isLoading) {
        return (
            <div className="flex-1 flex items-center justify-center p-8">
                <p className="text-muted-foreground">Loading tenant...</p>
            </div>
        );
    }

    if (!tenant) {
        return (
            <div className="flex-1 flex items-center justify-center p-8">
                <p className="text-muted-foreground">Tenant not found.</p>
            </div>
        );
    }

    return (
        <div className="flex-1 space-y-6 p-8 pt-6">
            {/* Header */}
            <div className="flex items-center gap-4">
                <Button variant="ghost" size="icon" onClick={() => navigate("/tenants")}>
                    <ArrowLeft className="h-4 w-4" />
                </Button>
                <div className="flex-1">
                    <div className="flex items-center gap-3">
                        <h2 className="text-3xl font-bold tracking-tight">{tenant.name}</h2>
                    </div>
                    <p className="text-sm text-muted-foreground font-mono">{tenant.slug}</p>
                </div>
            </div>

            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="credentials">Credentials</TabsTrigger>
                    <TabsTrigger value="locations">Locations</TabsTrigger>
                </TabsList>

                {/* Overview Tab */}
                <TabsContent value="overview" className="space-y-6">
                    <div className="grid gap-6">
                        {/* Tenant Details */}
                        <Card>
                            <CardHeader className="flex flex-row items-center justify-between space-y-0">
                                <div>
                                    <CardTitle>Tenant Details</CardTitle>
                                    <CardDescription>Basic information about this tenant</CardDescription>
                                </div>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 text-muted-foreground hover:text-foreground"
                                    onClick={() => setEditSheetOpen(true)}
                                >
                                    <Pencil className="h-4 w-4" />
                                    <span className="sr-only">Edit Tenant</span>
                                </Button>
                            </CardHeader>
                            <CardContent>
                                <dl className="grid grid-cols-2 gap-4 text-sm">
                                    <div className="space-y-1">
                                        <dt className="text-muted-foreground">Name</dt>
                                        <dd className="font-medium">{tenant.name}</dd>
                                    </div>
                                    <div className="space-y-1">
                                        <dt className="text-muted-foreground">Slug</dt>
                                        <dd className="font-mono">{tenant.slug}</dd>
                                    </div>
                                    <div className="space-y-1">
                                        <dt className="text-muted-foreground">Status</dt>
                                        <dd>
                                            <span
                                                className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tenant.is_active
                                                    ? "bg-green-50 text-green-700 ring-green-600/20 dark:bg-green-900/20 dark:text-green-400 dark:ring-green-900/10"
                                                    : "bg-gray-50 text-gray-600 ring-gray-500/10 dark:bg-gray-900/20 dark:text-gray-400 dark:ring-gray-700/10"
                                                    }`}
                                            >
                                                {tenant.is_active ? "Active" : "Inactive"}
                                            </span>
                                        </dd>
                                    </div>
                                    <div className="space-y-1">
                                        <dt className="text-muted-foreground">ID</dt>
                                        <dd className="font-mono text-xs text-muted-foreground">{tenant.id}</dd>
                                    </div>
                                </dl>
                            </CardContent>
                        </Card>

                        {/* Admin User */}
                        {tenant.user && (
                            <Card>
                                <CardHeader>
                                    <CardTitle>Admin User</CardTitle>
                                    <CardDescription>The primary admin user for this tenant</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <dl className="grid grid-cols-2 gap-4 text-sm">
                                        <div className="space-y-1">
                                            <dt className="text-muted-foreground">Email</dt>
                                            <dd className="font-medium">{tenant.user.email}</dd>
                                        </div>
                                        <div className="space-y-1">
                                            <dt className="text-muted-foreground">Role</dt>
                                            <dd className="capitalize text-muted-foreground">{tenant.user.role}</dd>
                                        </div>
                                        <div className="space-y-1">
                                            <dt className="text-muted-foreground">Status</dt>
                                            <dd>
                                                <span
                                                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tenant.user.is_active
                                                        ? "bg-green-50 text-green-700 ring-green-600/20 dark:bg-green-900/20 dark:text-green-400 dark:ring-green-900/10"
                                                        : "bg-gray-50 text-gray-600 ring-gray-500/10 dark:bg-gray-900/20 dark:text-gray-400 dark:ring-gray-700/10"
                                                        }`}
                                                >
                                                    {tenant.user.is_active ? "Active" : "Inactive"}
                                                </span>
                                            </dd>
                                        </div>
                                        <div className="space-y-1">
                                            <dt className="text-muted-foreground">User ID</dt>
                                            <dd className="font-mono text-xs text-muted-foreground">{tenant.user.id}</dd>
                                        </div>
                                    </dl>
                                </CardContent>
                            </Card>
                        )}
                    </div>

                    {/* Integrations Section */}
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-lg font-medium">Integrations</h3>
                        </div>
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-2">
                            <IntegrationCard
                                name="NexHealth"
                                description="Patient management system integration."
                                isConfigured={tenant.has_nexhealth_key}
                                hasSystemKey={tenant.has_system_nexhealth_key}
                                onConfigure={() => setActiveTab("credentials")}
                            />
                            <IntegrationCard
                                name="Retell AI"
                                description="Voice agent configuration."
                                isConfigured={tenant.has_retell_secret}
                                onConfigure={() => setActiveTab("locations")}
                            />
                        </div>
                    </div>
                </TabsContent>

                {/* Credentials Tab */}
                <TabsContent value="credentials">
                    <TenantCredentialsForm tenant={tenant} onUpdated={fetchTenant} />
                </TabsContent>

                {/* Locations Tab */}
                <TabsContent value="locations">
                    <LocationList tenantSlug={tenant.slug} />
                </TabsContent>
            </Tabs>

            {/* Edit Overview Sheet */}
            <Sheet open={editSheetOpen} onOpenChange={setEditSheetOpen}>
                <SheetContent>
                    <SheetHeader>
                        <SheetTitle>Edit Tenant</SheetTitle>
                        <SheetDescription>
                            Update the tenant name and status.
                        </SheetDescription>
                    </SheetHeader>
                    <div className="py-4">
                        <Form {...form}>
                            <form onSubmit={form.handleSubmit(onOverviewSubmit)} className="space-y-4">
                                <FormField
                                    control={form.control}
                                    name="name"
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>Tenant Name</FormLabel>
                                            <FormControl>
                                                <Input {...field} />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                                <FormField
                                    control={form.control}
                                    name="is_active"
                                    render={({ field }) => (
                                        <FormItem className="flex items-center justify-between rounded-lg border p-4">
                                            <div>
                                                <FormLabel className="text-base">Active</FormLabel>
                                                <p className="text-sm text-muted-foreground">
                                                    Inactive tenants cannot use the voice agent.
                                                </p>
                                            </div>
                                            <FormControl>
                                                <Switch
                                                    checked={field.value}
                                                    onCheckedChange={field.onChange}
                                                />
                                            </FormControl>
                                        </FormItem>
                                    )}
                                />
                                <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
                                    {form.formState.isSubmitting ? "Saving..." : "Save Changes"}
                                </Button>
                            </form>
                        </Form>
                    </div>
                </SheetContent>
            </Sheet>
        </div>
    );
}
