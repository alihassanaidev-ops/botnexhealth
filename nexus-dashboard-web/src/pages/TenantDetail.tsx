import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Pencil } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
                        <Badge variant={tenant.is_active ? "default" : "secondary"}>
                            {tenant.is_active ? "Active" : "Inactive"}
                        </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground font-mono">{tenant.slug}</p>
                </div>
            </div>

            {/* Tabs */}
            <Tabs defaultValue="overview">
                <TabsList>
                    <TabsTrigger value="overview">Overview</TabsTrigger>
                    <TabsTrigger value="credentials">Credentials</TabsTrigger>
                    <TabsTrigger value="locations">Locations</TabsTrigger>
                </TabsList>

                {/* Overview Tab */}
                <TabsContent value="overview" className="space-y-6">
                    <Card>
                        <CardHeader className="flex flex-row items-center justify-between space-y-0">
                            <div>
                                <CardTitle>Tenant Details</CardTitle>
                                <CardDescription>Basic information about this tenant</CardDescription>
                            </div>
                            <Button variant="outline" size="sm" onClick={() => setEditSheetOpen(true)}>
                                <Pencil className="mr-1 h-3 w-3" /> Edit
                            </Button>
                        </CardHeader>
                        <CardContent>
                            <dl className="grid grid-cols-2 gap-4 text-sm">
                                <div>
                                    <dt className="text-muted-foreground">Name</dt>
                                    <dd className="font-medium">{tenant.name}</dd>
                                </div>
                                <div>
                                    <dt className="text-muted-foreground">Slug</dt>
                                    <dd className="font-mono">{tenant.slug}</dd>
                                </div>
                                <div>
                                    <dt className="text-muted-foreground">Status</dt>
                                    <dd>
                                        <Badge variant={tenant.is_active ? "default" : "secondary"}>
                                            {tenant.is_active ? "Active" : "Inactive"}
                                        </Badge>
                                    </dd>
                                </div>
                                <div>
                                    <dt className="text-muted-foreground">ID</dt>
                                    <dd className="font-mono text-xs">{tenant.id}</dd>
                                </div>
                            </dl>
                        </CardContent>
                    </Card>

                    {tenant.user && (
                        <Card>
                            <CardHeader>
                                <CardTitle>Admin User</CardTitle>
                                <CardDescription>The primary admin user for this tenant</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <dl className="grid grid-cols-2 gap-4 text-sm">
                                    <div>
                                        <dt className="text-muted-foreground">Email</dt>
                                        <dd className="font-medium">{tenant.user.email}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-muted-foreground">Role</dt>
                                        <dd className="capitalize">{tenant.user.role}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-muted-foreground">Status</dt>
                                        <dd>
                                            <Badge variant={tenant.user.is_active ? "default" : "secondary"}>
                                                {tenant.user.is_active ? "Active" : "Inactive"}
                                            </Badge>
                                        </dd>
                                    </div>
                                    <div>
                                        <dt className="text-muted-foreground">User ID</dt>
                                        <dd className="font-mono text-xs">{tenant.user.id}</dd>
                                    </div>
                                </dl>
                            </CardContent>
                        </Card>
                    )}

                    {/* Integration Status Summary */}
                    <Card>
                        <CardHeader>
                            <CardTitle>Integration Status</CardTitle>
                            <CardDescription>Quick overview of configured integrations</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                                <div className="flex items-center gap-2">
                                    <div className={`h-2 w-2 rounded-full ${tenant.has_nexhealth_key ? "bg-green-500" : "bg-gray-300"}`} />
                                    <span className="text-sm">NexHealth</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <div className={`h-2 w-2 rounded-full ${tenant.has_ghl_key ? "bg-green-500" : "bg-gray-300"}`} />
                                    <span className="text-sm">GoHighLevel</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <div className={`h-2 w-2 rounded-full ${tenant.has_retell_secret ? "bg-green-500" : "bg-gray-300"}`} />
                                    <span className="text-sm">Retell AI</span>
                                </div>
                                <div className="flex items-center gap-2">
                                    <div className={`h-2 w-2 rounded-full ${tenant.has_sikka_credentials ? "bg-green-500" : "bg-gray-300"}`} />
                                    <span className="text-sm">Sikka</span>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
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
