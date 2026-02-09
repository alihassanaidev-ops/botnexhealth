import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Check, Pencil, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { toast } from "sonner";
import api from "@/lib/api";
import type { TenantDetail } from "@/types";

const credentialsSchema = z.object({
    nexhealth_api_key: z.string().optional(),
    nexhealth_subdomain: z.string().optional(),
    nexhealth_location_id: z.string().optional(),
    ghl_api_key: z.string().optional(),
    ghl_location_id: z.string().optional(),
    ghl_custom_fields: z.string().optional(),
    retell_agent_id: z.string().optional(),
    retell_api_secret: z.string().optional(),
    sikka_app_id: z.string().optional(),
    sikka_app_secret: z.string().optional(),
    sikka_office_id: z.string().optional(),
});

type CredentialsFormValues = z.infer<typeof credentialsSchema>;

type SectionKey = "nexhealth" | "ghl" | "retell" | "sikka";

interface TenantCredentialsFormProps {
    tenant: TenantDetail;
    onUpdated: () => void;
}

export function TenantCredentialsForm({ tenant, onUpdated }: TenantCredentialsFormProps) {
    const [editingSection, setEditingSection] = useState<SectionKey | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    const form = useForm<CredentialsFormValues>({
        resolver: zodResolver(credentialsSchema),
        defaultValues: {
            nexhealth_subdomain: tenant.nexhealth_subdomain || "",
            nexhealth_location_id: tenant.nexhealth_location_id || "",
            nexhealth_api_key: "",
            ghl_location_id: tenant.ghl_location_id || "",
            ghl_api_key: "",
            ghl_custom_fields: tenant.ghl_custom_fields
                ? JSON.stringify(tenant.ghl_custom_fields, null, 2)
                : "",
            retell_agent_id: tenant.retell_agent_id || "",
            retell_api_secret: "",
            sikka_office_id: tenant.sikka_office_id || "",
            sikka_app_id: "",
            sikka_app_secret: "",
        },
    });

    async function onSubmit(values: CredentialsFormValues) {
        setIsSaving(true);
        try {
            const payload: Record<string, unknown> = {};

            // Only include fields from the active section that have values
            const sectionFields: Record<SectionKey, (keyof CredentialsFormValues)[]> = {
                nexhealth: ["nexhealth_api_key", "nexhealth_subdomain", "nexhealth_location_id"],
                ghl: ["ghl_api_key", "ghl_location_id", "ghl_custom_fields"],
                retell: ["retell_agent_id", "retell_api_secret"],
                sikka: ["sikka_app_id", "sikka_app_secret", "sikka_office_id"],
            };

            if (editingSection) {
                for (const field of sectionFields[editingSection]) {
                    const val = values[field];
                    if (val !== undefined && val !== "") {
                        if (field === "ghl_custom_fields") {
                            try {
                                payload[field] = JSON.parse(val as string);
                            } catch {
                                toast.error("Invalid JSON in GHL Custom Fields");
                                setIsSaving(false);
                                return;
                            }
                        } else {
                            payload[field] = val;
                        }
                    }
                }
            }

            if (Object.keys(payload).length === 0) {
                toast.info("No changes to save");
                setIsSaving(false);
                return;
            }

            await api.patch(`/admin/tenants/${tenant.slug}`, payload);
            toast.success("Credentials updated");
            setEditingSection(null);
            onUpdated();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || "Failed to update credentials");
        } finally {
            setIsSaving(false);
        }
    }

    const ConfiguredBadge = ({ configured }: { configured: boolean }) => (
        <Badge variant={configured ? "default" : "outline"} className="ml-2">
            {configured ? (
                <><Check className="mr-1 h-3 w-3" /> Configured</>
            ) : (
                "Not configured"
            )}
        </Badge>
    );

    const SectionEditButton = ({ section }: { section: SectionKey }) => {
        if (editingSection === section) {
            return (
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditingSection(null)}
                >
                    <X className="mr-1 h-3 w-3" /> Cancel
                </Button>
            );
        }
        return (
            <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setEditingSection(section)}
                disabled={editingSection !== null && editingSection !== section}
            >
                <Pencil className="mr-1 h-3 w-3" /> Edit
            </Button>
        );
    };

    return (
        <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
                {/* NexHealth */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <div>
                            <CardTitle className="text-base">NexHealth</CardTitle>
                            <CardDescription>PMS integration credentials</CardDescription>
                        </div>
                        <div className="flex items-center">
                            <ConfiguredBadge configured={tenant.has_nexhealth_key} />
                            <SectionEditButton section="nexhealth" />
                        </div>
                    </CardHeader>
                    {editingSection === "nexhealth" && (
                        <CardContent className="space-y-4">
                            <FormField
                                control={form.control}
                                name="nexhealth_api_key"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>API Key</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                placeholder={tenant.has_nexhealth_key ? "••••••••  (leave blank to keep)" : "Enter API key"}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="nexhealth_subdomain"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Subdomain</FormLabel>
                                        <FormControl>
                                            <Input placeholder="e.g. acme-dental" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="nexhealth_location_id"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Location ID</FormLabel>
                                        <FormControl>
                                            <Input placeholder="e.g. 12345" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </CardContent>
                    )}
                    {editingSection !== "nexhealth" && (tenant.nexhealth_subdomain || tenant.nexhealth_location_id) && (
                        <CardContent>
                            <div className="text-sm text-muted-foreground space-y-1">
                                {tenant.nexhealth_subdomain && <p>Subdomain: <span className="font-mono">{tenant.nexhealth_subdomain}</span></p>}
                                {tenant.nexhealth_location_id && <p>Location ID: <span className="font-mono">{tenant.nexhealth_location_id}</span></p>}
                            </div>
                        </CardContent>
                    )}
                </Card>

                {/* GoHighLevel */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <div>
                            <CardTitle className="text-base">GoHighLevel</CardTitle>
                            <CardDescription>CRM integration credentials</CardDescription>
                        </div>
                        <div className="flex items-center">
                            <ConfiguredBadge configured={tenant.has_ghl_key} />
                            <SectionEditButton section="ghl" />
                        </div>
                    </CardHeader>
                    {editingSection === "ghl" && (
                        <CardContent className="space-y-4">
                            <FormField
                                control={form.control}
                                name="ghl_api_key"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>API Key</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                placeholder={tenant.has_ghl_key ? "••••••••  (leave blank to keep)" : "Enter API key"}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="ghl_location_id"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Location ID</FormLabel>
                                        <FormControl>
                                            <Input placeholder="e.g. loc_abc123" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="ghl_custom_fields"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Custom Fields (JSON)</FormLabel>
                                        <FormControl>
                                            <Textarea
                                                placeholder='{"field_name": "field_id"}'
                                                className="font-mono text-sm"
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </CardContent>
                    )}
                    {editingSection !== "ghl" && tenant.ghl_location_id && (
                        <CardContent>
                            <div className="text-sm text-muted-foreground">
                                <p>Location ID: <span className="font-mono">{tenant.ghl_location_id}</span></p>
                            </div>
                        </CardContent>
                    )}
                </Card>

                {/* Retell */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <div>
                            <CardTitle className="text-base">Retell AI</CardTitle>
                            <CardDescription>Voice agent credentials</CardDescription>
                        </div>
                        <div className="flex items-center">
                            <ConfiguredBadge configured={tenant.has_retell_secret} />
                            <SectionEditButton section="retell" />
                        </div>
                    </CardHeader>
                    {editingSection === "retell" && (
                        <CardContent className="space-y-4">
                            <FormField
                                control={form.control}
                                name="retell_agent_id"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Agent ID</FormLabel>
                                        <FormControl>
                                            <Input placeholder="e.g. agent_xxx" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="retell_api_secret"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>API Secret</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                placeholder={tenant.has_retell_secret ? "••••••••  (leave blank to keep)" : "Enter API secret"}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </CardContent>
                    )}
                    {editingSection !== "retell" && tenant.retell_agent_id && (
                        <CardContent>
                            <div className="text-sm text-muted-foreground">
                                <p>Agent ID: <span className="font-mono">{tenant.retell_agent_id}</span></p>
                            </div>
                        </CardContent>
                    )}
                </Card>

                {/* Sikka */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <div>
                            <CardTitle className="text-base">Sikka</CardTitle>
                            <CardDescription>PMS adapter credentials</CardDescription>
                        </div>
                        <div className="flex items-center">
                            <ConfiguredBadge configured={tenant.has_sikka_credentials} />
                            <SectionEditButton section="sikka" />
                        </div>
                    </CardHeader>
                    {editingSection === "sikka" && (
                        <CardContent className="space-y-4">
                            <FormField
                                control={form.control}
                                name="sikka_app_id"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>App ID</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                placeholder={tenant.has_sikka_credentials ? "••••••••  (leave blank to keep)" : "Enter App ID"}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="sikka_app_secret"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>App Secret</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                placeholder={tenant.has_sikka_credentials ? "••••••••  (leave blank to keep)" : "Enter App Secret"}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="sikka_office_id"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Office ID</FormLabel>
                                        <FormControl>
                                            <Input placeholder="e.g. 12345" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </CardContent>
                    )}
                    {editingSection !== "sikka" && tenant.sikka_office_id && (
                        <CardContent>
                            <div className="text-sm text-muted-foreground">
                                <p>Office ID: <span className="font-mono">{tenant.sikka_office_id}</span></p>
                            </div>
                        </CardContent>
                    )}
                </Card>

                {editingSection && (
                    <div className="flex justify-end">
                        <Button type="submit" disabled={isSaving}>
                            {isSaving ? "Saving..." : "Save Credentials"}
                        </Button>
                    </div>
                )}
            </form>
        </Form>
    );
}
