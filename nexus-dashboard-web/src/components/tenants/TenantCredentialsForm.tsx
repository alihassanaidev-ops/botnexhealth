import { useState } from "react";
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
        <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${configured
                ? "bg-green-50 text-green-700 ring-green-600/20 dark:bg-green-900/20 dark:text-green-400 dark:ring-green-900/10"
                : "bg-gray-50 text-gray-600 ring-gray-500/10 dark:bg-gray-900/20 dark:text-gray-400 dark:ring-gray-700/10"
                }`}
        >
            {configured ? "Configured" : "Not configured"}
        </span>
    );

    const SectionEditButton = ({ section }: { section: SectionKey }) => {
        if (editingSection === section) {
            return (
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2 text-muted-foreground hover:text-foreground"
                    onClick={() => setEditingSection(null)}
                >
                    <X className="mr-1 h-3.5 w-3.5" /> Cancel
                </Button>
            );
        }
        return (
            <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 px-2 text-muted-foreground hover:text-foreground"
                onClick={() => setEditingSection(section)}
                disabled={editingSection !== null && editingSection !== section}
            >
                <Pencil className="mr-1 h-3.5 w-3.5" /> Edit
            </Button>
        );
    };

    const CredentialCard = ({
        title,
        description,
        section,
        configured,
        children,
    }: {
        title: string;
        description: string;
        section: SectionKey;
        configured: boolean;
        children: React.ReactNode;
    }) => (
        <Card className="group overflow-hidden transition-colors hover:border-foreground/20">
            <CardHeader className="flex flex-row items-start justify-between space-y-0 p-6">
                <div className="space-y-1">
                    <CardTitle className="text-sm font-medium leading-none">{title}</CardTitle>
                    <CardDescription className="text-xs">{description}</CardDescription>
                </div>
                <div className="flex items-center gap-2">
                    <ConfiguredBadge configured={configured} />
                    <SectionEditButton section={section} />
                </div>
            </CardHeader>
            <CardContent className="p-6 pt-0">
                {editingSection === section ? (
                    <div className="space-y-4 pt-2">
                        {children}
                    </div>
                ) : (
                    <div className="text-sm text-muted-foreground">
                        {configured ? (
                            <div className="grid gap-1">
                                {section === "nexhealth" && (
                                    <>
                                        <div className="flex justify-between py-1 border-b border-border/50 last:border-0">
                                            <span className="font-normal text-muted-foreground">Subdomain</span>
                                            <span className="font-mono text-foreground">{tenant.nexhealth_subdomain || "—"}</span>
                                        </div>
                                        <div className="flex justify-between py-1 border-b border-border/50 last:border-0">
                                            <span className="font-normal text-muted-foreground">Location ID</span>
                                            <span className="font-mono text-foreground">{tenant.nexhealth_location_id || "—"}</span>
                                        </div>
                                    </>
                                )}
                                {section === "ghl" && (
                                    <div className="flex justify-between py-1 border-b border-border/50 last:border-0">
                                        <span className="font-normal text-muted-foreground">Location ID</span>
                                        <span className="font-mono text-foreground">{tenant.ghl_location_id || "—"}</span>
                                    </div>
                                )}
                                {section === "retell" && (
                                    <div className="flex justify-between py-1 border-b border-border/50 last:border-0">
                                        <span className="font-normal text-muted-foreground">Agent ID</span>
                                        <span className="font-mono text-foreground">{tenant.retell_agent_id || "—"}</span>
                                    </div>
                                )}
                                {section === "sikka" && (
                                    <div className="flex justify-between py-1 border-b border-border/50 last:border-0">
                                        <span className="font-normal text-muted-foreground">Office ID</span>
                                        <span className="font-mono text-foreground">{tenant.sikka_office_id || "—"}</span>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground/60 italic">No credentials configured.</p>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );

    return (
        <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
                <CredentialCard
                    title="NexHealth"
                    description="Patient management system integration."
                    section="nexhealth"
                    configured={tenant.has_nexhealth_key}
                >
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
                    <div className="grid grid-cols-2 gap-4">
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
                </CredentialCard>

                <CredentialCard
                    title="GoHighLevel"
                    description="CRM and marketing automation."
                    section="ghl"
                    configured={tenant.has_ghl_key}
                >
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
                </CredentialCard>

                <CredentialCard
                    title="Retell AI"
                    description="Voice agent configuration."
                    section="retell"
                    configured={tenant.has_retell_secret}
                >
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
                </CredentialCard>

                <CredentialCard
                    title="Sikka"
                    description="Universal PMS adapter."
                    section="sikka"
                    configured={tenant.has_sikka_credentials}
                >
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
                </CredentialCard>

                {editingSection && (
                    <div className="flex justify-end pt-4 border-t">
                        <Button type="submit" disabled={isSaving}>
                            {isSaving ? "Saving..." : "Save Credentials"}
                        </Button>
                    </div>
                )}
            </form>
        </Form>
    );
}
