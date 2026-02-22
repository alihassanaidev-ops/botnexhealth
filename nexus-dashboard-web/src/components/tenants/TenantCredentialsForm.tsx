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
import {
    Card,
} from "@/components/ui/card";
import { toast } from "sonner";
import api from "@/lib/api";
import type { TenantDetail } from "@/types";

const credentialsSchema = z.object({
    nexhealth_api_key: z.string().optional(),
    retell_api_secret: z.string().optional(),
});

type CredentialsFormValues = z.infer<typeof credentialsSchema>;

type SectionKey = "nexhealth" | "retell";

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
            nexhealth_api_key: "",
            retell_api_secret: "",
        },
    });

    // Reset form when tenant data is refreshed (e.g. after save)
    useEffect(() => {
        form.reset({
            nexhealth_api_key: "",
            retell_api_secret: "",
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tenant]);

    async function onSubmit(values: CredentialsFormValues) {
        setIsSaving(true);
        try {
            const payload: Record<string, unknown> = {};

            // Only include fields from the active section that have values
            const sectionFields: Record<SectionKey, (keyof CredentialsFormValues)[]> = {
                nexhealth: ["nexhealth_api_key"],
                retell: ["retell_api_secret"],
            };

            if (editingSection) {
                for (const field of sectionFields[editingSection]) {
                    const val = values[field];
                    if (val !== undefined && val !== "") {
                        payload[field] = val;
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
