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
import type { InstitutionDetail } from "@/types";

const credentialsSchema = z.object({
    nexhealth_api_key: z.string().optional(),
});

type CredentialsFormValues = z.infer<typeof credentialsSchema>;

type SectionKey = "nexhealth";

interface InstitutionCredentialsFormProps {
    institution: InstitutionDetail;
    onUpdated: () => void;
}

export function TenantCredentialsForm({ institution, onUpdated }: InstitutionCredentialsFormProps) {
    const [editingSection, setEditingSection] = useState<SectionKey | null>(null);
    const [isSaving, setIsSaving] = useState(false);

    const form = useForm<CredentialsFormValues>({
        resolver: zodResolver(credentialsSchema),
        defaultValues: {
            nexhealth_api_key: "",
        },
    });

    // Reset form when institution data is refreshed (e.g. after save)
    useEffect(() => {
        form.reset({
            nexhealth_api_key: "",
        });
    }, [institution, form]);

    async function onSubmit(values: CredentialsFormValues) {
        setIsSaving(true);
        try {
            const payload: Record<string, unknown> = {};

            // Only include fields from the active section that have values
            const sectionFields: Record<SectionKey, (keyof CredentialsFormValues)[]> = {
                nexhealth: ["nexhealth_api_key"],
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

            await api.patch(`/admin/institutions/${institution.slug}`, payload);
            toast.success("Credentials updated");
            setEditingSection(null);
            onUpdated();
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to update credentials");
        } finally {
            setIsSaving(false);
        }
    }

    const IntegrationStatus = ({ configured, hasSystemKey = false }: { configured: boolean; hasSystemKey?: boolean }) => {
        let statusColor = "bg-muted-foreground/40";
        let statusText = "Not Connected";

        if (configured) {
            statusColor = "bg-primary";
            statusText = "Connected";
        } else if (hasSystemKey) {
            statusColor = "bg-primary/60";
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
            <Card className={`group overflow-hidden border-primary/15 bg-gradient-to-br from-card to-accent/20 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-md hover:shadow-primary/10 ${isEditing ? "ring-1 ring-primary/40 border-primary/50" : ""}`}>
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
                                className="h-8 w-8 text-muted-foreground hover:bg-primary/10 hover:text-primary"
                                onClick={() => setEditingSection(null)}
                            >
                                <X className="h-4 w-4" />
                            </Button>
                        ) : (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-muted-foreground hover:bg-primary/10 hover:text-primary"
                                onClick={() => setEditingSection(section)}
                            >
                                <Pencil className="h-4 w-4" />
                            </Button>
                        )}
                    </div>
                </div>

                {isEditing && (
                    <div className="animate-in slide-in-from-top-2 fade-in space-y-4 border-t border-primary/15 bg-background/60 px-4 py-6 duration-200">
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
                    configured={institution.has_nexhealth_key}
                    hasSystemKey={institution.has_system_nexhealth_key}
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
                                            placeholder={institution.has_nexhealth_key ? "••••••••" : "Enter API key"}
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
