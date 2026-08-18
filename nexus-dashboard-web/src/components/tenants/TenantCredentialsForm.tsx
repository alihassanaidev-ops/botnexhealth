import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { AlertCircle, CheckCircle2, Loader2, Pencil, X } from "lucide-react";
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
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import api from "@/lib/api";
import type { InstitutionDetail, Location } from "@/types";

const credentialsSchema = z.object({
    credential_mode: z.enum(["platform", "institution"]),
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
    const [locations, setLocations] = useState<Location[]>([]);
    const [selectedLocationId, setSelectedLocationId] = useState<string>("");
    const [isVerifying, setIsVerifying] = useState(false);
    const [verification, setVerification] = useState<{
        ok: boolean;
        message: string;
        credential_mode: string;
        api_key_hash?: string | null;
    } | null>(null);

    const form = useForm<CredentialsFormValues>({
        resolver: zodResolver(credentialsSchema),
        defaultValues: {
            credential_mode: institution.has_nexhealth_key ? "institution" : "platform",
            nexhealth_api_key: "",
        },
    });
    const credentialMode = form.watch("credential_mode");

    // Reset form when institution data is refreshed (e.g. after save)
    useEffect(() => {
        form.reset({
            credential_mode: institution.has_nexhealth_key ? "institution" : "platform",
            nexhealth_api_key: "",
        });
        setVerification(null);
    }, [institution, form]);

    useEffect(() => {
        let cancelled = false;

        async function fetchLocations() {
            try {
                const { data } = await api.get<Location[]>(
                    `/admin/institutions/${institution.slug}/locations`
                );
                if (cancelled) return;
                setLocations(data);
                const firstNexHealthLocation = data.find(
                    (loc) => loc.nexhealth_subdomain && loc.nexhealth_location_id
                );
                setSelectedLocationId(firstNexHealthLocation?.id || data[0]?.id || "");
            } catch {
                if (!cancelled) setLocations([]);
            }
        }

        fetchLocations();
        return () => {
            cancelled = true;
        };
    }, [institution.slug]);

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
            if (editingSection === "nexhealth" && values.credential_mode === "platform") {
                payload.nexhealth_api_key = null;
            }

            if (Object.keys(payload).length === 0) {
                toast.info("No changes to save");
                setIsSaving(false);
                return;
            }

            await api.patch(`/admin/institutions/${institution.slug}`, payload);
            toast.success("Credentials updated");
            setEditingSection(null);
            setVerification(null);
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

    async function handleVerify() {
        const selectedLocation = locations.find((loc) => loc.id === selectedLocationId);
        const apiKey = form.getValues("nexhealth_api_key")?.trim();
        if (credentialMode === "institution" && !institution.has_nexhealth_key && !apiKey) {
            toast.error("Enter an API key before verifying");
            return;
        }
        if (!selectedLocation?.nexhealth_subdomain || !selectedLocation.nexhealth_location_id) {
            toast.error("Select a location with NexHealth subdomain and location ID");
            return;
        }

        setIsVerifying(true);
        setVerification(null);
        try {
            const { data } = await api.post(`/admin/institutions/${institution.slug}/nexhealth/verify`, {
                nexhealth_api_key: apiKey || undefined,
                subdomain: selectedLocation.nexhealth_subdomain,
                location_id: selectedLocation.nexhealth_location_id,
            });
            setVerification(data);
            if (data.ok) {
                toast.success("NexHealth credentials verified");
            } else {
                toast.error(data.message || "NexHealth verification failed");
            }
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            const message = error?.response?.data?.detail || "NexHealth verification failed";
            setVerification({ ok: false, message, credential_mode: credentialMode });
            toast.error(message);
        } finally {
            setIsVerifying(false);
        }
    }

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
            <Card className={`group overflow-hidden border-border bg-gradient-to-br from-card to-accent/20 transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-md hover:shadow-primary/10 ${isEditing ? "ring-1 ring-primary/40 border-border" : ""}`}>
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
                    <div className="animate-in slide-in-from-top-2 fade-in space-y-4 border-t border-border bg-background/60 px-4 py-6 duration-200">
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
                        <div className="grid gap-2">
                            <Label>Credential Mode</Label>
                            <Select
                                value={credentialMode}
                                onValueChange={(value) => {
                                    form.setValue("credential_mode", value as CredentialsFormValues["credential_mode"]);
                                    setVerification(null);
                                }}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="platform">
                                        Platform key
                                    </SelectItem>
                                    <SelectItem value="institution">
                                        Clinic-owned key
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <FormField
                            control={form.control}
                            name="nexhealth_api_key"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>Clinic API Key</FormLabel>
                                    <FormControl>
                                        <Input
                                            type="password"
                                            placeholder={institution.has_nexhealth_key ? "••••••••" : "Enter API key"}
                                            disabled={credentialMode === "platform"}
                                            {...field}
                                        />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <div className="grid gap-2">
                            <Label>Verify Against Location</Label>
                            <Select
                                value={selectedLocationId}
                                onValueChange={setSelectedLocationId}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select a location" />
                                </SelectTrigger>
                                <SelectContent>
                                    {locations.map((loc) => (
                                        <SelectItem key={loc.id} value={loc.id}>
                                            {loc.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex flex-wrap items-center gap-3">
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={handleVerify}
                                disabled={isVerifying || !selectedLocationId}
                            >
                                {isVerifying ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : verification?.ok ? (
                                    <CheckCircle2 className="mr-2 h-4 w-4" />
                                ) : (
                                    <AlertCircle className="mr-2 h-4 w-4" />
                                )}
                                {isVerifying ? "Verifying..." : "Verify"}
                            </Button>
                            {verification && (
                                <span className={`text-xs font-medium ${verification.ok ? "text-green-600" : "text-destructive"}`}>
                                    {verification.message}
                                </span>
                            )}
                        </div>
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
