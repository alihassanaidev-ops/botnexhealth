import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Pencil, X } from "lucide-react";
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
import {
    Card,
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

    // Reset form when tenant data is refreshed (e.g. after save)
    useEffect(() => {
        form.reset({
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
        });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tenant]);

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

    const IntegrationStatus = ({ configured, hasSystemKey = false }: { configured: boolean; hasSystemKey?: boolean }) => {
        let statusColor = "bg-neutral-300 dark:bg-neutral-600";
        let statusText = "Not Connected";

        if (configured) {
            statusColor = "bg-green-500";
            statusText = "Connected";
        } else if (hasSystemKey) {
            statusColor = "bg-green-500/50";
            statusText = "Connected (System)";
        }

        return (
            <div className="flex items-center gap-1.5 mt-2">
                <div className={`h-1.5 w-1.5 rounded-full ${statusColor}`} />
                <span className="text-xs text-muted-foreground font-medium">
                    {statusText}
                </span>
            </div>
        );
    };

    const CredentialCard = ({
        title,
        description,
        section,
        configured,
        hasSystemKey = false,
        children,
    }: {
        title: string;
        description: string;
        section: SectionKey;
        configured: boolean;
        hasSystemKey?: boolean;
        children: React.ReactNode;
    }) => {
        const isEditing = editingSection === section;

        return (
            <Card className={`group overflow-hidden transition-all duration-200 border-border/60 hover:border-border/80 hover:bg-muted/50 hover:shadow-sm ${isEditing ? "ring-1 ring-ring border-ring" : ""}`}>
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between p-4 gap-4">
                    <div className="flex-1 space-y-1">
                        <div className="flex items-center gap-2">
                            <h3 className="text-sm font-semibold text-foreground">{title}</h3>
                        </div>
                        <p className="text-xs text-muted-foreground max-w-md">
                            {description}
                        </p>
                        <IntegrationStatus configured={configured} hasSystemKey={hasSystemKey} />
                    </div>

                    <div className="flex items-center gap-2 self-start sm:self-center shrink-0">
                        {isEditing ? (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-muted-foreground hover:text-foreground"
                                onClick={() => setEditingSection(null)}
                            >
                                <X className="h-4 w-4" />
                            </Button>
                        ) : (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-muted-foreground hover:text-foreground hover:bg-background/80"
                                onClick={() => setEditingSection(section)}
                            >
                                <Pencil className="h-4 w-4" />
                            </Button>
                        )}
                    </div>
                </div>

                {isEditing && (
                    <div className="border-t bg-background/50 px-4 py-6 space-y-4 animate-in slide-in-from-top-2 fade-in duration-200">
                        {children}
                    </div>
                )}
            </Card>
        );
    };

    return (
        <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-3">
                <CredentialCard
                    title="NexHealth"
                    description="Sync patients, appointments, and providers from the practice management system."
                    section="nexhealth"
                    configured={tenant.has_nexhealth_key}
                    hasSystemKey={tenant.has_system_nexhealth_key}
                >
                    <div className="grid gap-4">
                        <FormField
                            control={form.control}
                            name="nexhealth_api_key"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>API Key</FormLabel>
                                    <FormControl>
                                        <Input
                                            type="password"
                                            placeholder={tenant.has_nexhealth_key ? "••••••••" : "Enter API key"}
                                            {...field}
                                        />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <FormField
                                control={form.control}
                                name="nexhealth_subdomain"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Subdomain</FormLabel>
                                        <FormControl>
                                            <Input placeholder="acme-dental" {...field} />
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
                                            <Input placeholder="12345" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>
                    </div>
                </CredentialCard>

                <CredentialCard
                    title="GoHighLevel"
                    description="Manage leads, automation workflows, and customer relationships."
                    section="ghl"
                    configured={tenant.has_ghl_key}
                >
                    <div className="grid gap-4">
                        <FormField
                            control={form.control}
                            name="ghl_api_key"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>API Key</FormLabel>
                                    <FormControl>
                                        <Input
                                            type="password"
                                            placeholder={tenant.has_ghl_key ? "••••••••" : "Enter API key"}
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
                                        <Input placeholder="location_id" {...field} />
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
                                            placeholder='{"field_name": "id"}'
                                            className="font-mono text-xs"
                                            rows={3}
                                            {...field}
                                        />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </div>
                </CredentialCard>

                <CredentialCard
                    title="Retell AI"
                    description="Configure voice agents for inbound and outbound calls."
                    section="retell"
                    configured={tenant.has_retell_secret}
                >
                    <div className="grid gap-4">
                        <FormField
                            control={form.control}
                            name="retell_agent_id"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>Agent ID</FormLabel>
                                    <FormControl>
                                        <Input placeholder="agent_xxx" {...field} />
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
                                            placeholder={tenant.has_retell_secret ? "••••••••" : "Enter secret"}
                                            {...field}
                                        />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </div>
                </CredentialCard>

                <CredentialCard
                    title="Sikka"
                    description="Universal adapter for connecting legacy practice management systems."
                    section="sikka"
                    configured={tenant.has_sikka_credentials}
                >
                    <div className="grid gap-4">
                        <FormField
                            control={form.control}
                            name="sikka_app_id"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>App ID</FormLabel>
                                    <FormControl>
                                        <Input
                                            type="password"
                                            placeholder={tenant.has_sikka_credentials ? "••••••••" : "Enter App ID"}
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
                                            placeholder={tenant.has_sikka_credentials ? "••••••••" : "Enter App Secret"}
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
                                        <Input placeholder="office_id" {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </div>
                </CredentialCard>

                {editingSection && (
                    <div className="flex items-center justify-end pt-2">
                        <Button type="submit" disabled={isSaving} size="sm">
                            {isSaving ? "Saving..." : "Save Changes"}
                        </Button>
                    </div>
                )}
            </form>
        </Form>
    );
}
