import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { AlertCircle, CheckCircle2, KeyRound, Loader2, Pencil, Trash2, X } from "lucide-react";
import { Button } from "@/components/foundation/compat/button";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/foundation/compat/form";
import { Input } from "@/components/foundation/compat/input";
import {
    Card,
} from "@/components/foundation/compat/card";
import { Label } from "@/components/foundation/compat/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/foundation/compat/select";
import { toast } from "sonner";
import api from "@/lib/api";
import {
    clearInstitutionTwilioProvisioning,
    getInstitutionProvisioning,
    updateInstitutionTwilioProvisioning,
} from "@/lib/admin-api";
import type { InstitutionDetail, InstitutionProvisioningStatus } from "@/types";

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

const twilioCredentialsSchema = z.object({
    twilio_account_sid: z.string().trim().min(1, "Account SID is required"),
    twilio_auth_token: z.string().trim().min(1, "Auth token is required"),
});

type TwilioCredentialsFormValues = z.infer<typeof twilioCredentialsSchema>;

function TwilioCredentialsCard({ institutionSlug }: { institutionSlug: string }) {
    const [status, setStatus] = useState<InstitutionProvisioningStatus | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isEditing, setIsEditing] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isClearing, setIsClearing] = useState(false);

    const form = useForm<TwilioCredentialsFormValues>({
        resolver: zodResolver(twilioCredentialsSchema),
        defaultValues: {
            twilio_account_sid: "",
            twilio_auth_token: "",
        },
    });

    useEffect(() => {
        let cancelled = false;

        async function loadStatus() {
            setIsLoading(true);
            try {
                const provisioning = await getInstitutionProvisioning(institutionSlug);
                if (!cancelled) setStatus(provisioning);
            } catch (err: unknown) {
                const error = err as { response?: { data?: { detail?: string } } };
                if (!cancelled) {
                    toast.error(error.response?.data?.detail || "Failed to load Twilio configuration");
                }
            } finally {
                if (!cancelled) setIsLoading(false);
            }
        }

        loadStatus();
        return () => {
            cancelled = true;
        };
    }, [institutionSlug]);

    async function saveTwilioCredentials(values: TwilioCredentialsFormValues) {
        setIsSaving(true);
        try {
            const updated = await updateInstitutionTwilioProvisioning(institutionSlug, values);
            setStatus(updated);
            form.reset();
            setIsEditing(false);
            toast.success("Twilio account connected");
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error.response?.data?.detail || "Failed to save Twilio credentials");
        } finally {
            setIsSaving(false);
        }
    }

    async function clearTwilioCredentials() {
        if (!window.confirm("Disconnect this institution's Twilio account? Existing location number assignments will remain, but SMS will use the legacy platform fallback.")) {
            return;
        }

        setIsClearing(true);
        try {
            await clearInstitutionTwilioProvisioning(institutionSlug);
            const updated = await getInstitutionProvisioning(institutionSlug);
            setStatus(updated);
            form.reset();
            setIsEditing(false);
            toast.success("Twilio account disconnected");
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error.response?.data?.detail || "Failed to disconnect Twilio account");
        } finally {
            setIsClearing(false);
        }
    }

    return (
        <Card className="overflow-hidden">
            <div className="flex flex-col items-start justify-between gap-4 p-4 sm:flex-row sm:items-center">
                <div className="space-y-1">
                    <div className="flex items-center gap-2">
                        <KeyRound className="h-4 w-4 text-muted-foreground" />
                        <h3 className="text-sm font-semibold">Twilio SMS Account</h3>
                    </div>
                    <p className="max-w-xl text-xs text-muted-foreground">
                        Connect the Twilio account used by this institution. Its locations can only select SMS numbers owned by this account.
                    </p>
                    <div className="flex items-center gap-1.5 pt-1">
                        <div className={`h-1.5 w-1.5 rounded-full ${status?.twilio_configured ? "bg-primary" : "bg-muted-foreground/40"}`} />
                        <span className="text-xs font-medium text-muted-foreground">
                            {isLoading
                                ? "Checking..."
                                : status?.twilio_configured
                                    ? `Connected (${status.twilio_account_sid_masked})`
                                    : "Not connected"}
                        </span>
                    </div>
                </div>

                <div className="flex shrink-0 items-center gap-2">
                    {status?.twilio_configured && (
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={clearTwilioCredentials}
                            disabled={isClearing || isSaving}
                            title="Disconnect Twilio account"
                        >
                            {isClearing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4 text-destructive" />}
                        </Button>
                    )}
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => setIsEditing(current => !current)}
                        disabled={isLoading || isClearing}
                        title={status?.twilio_configured ? "Update Twilio credentials" : "Connect Twilio account"}
                    >
                        {isEditing ? <X className="h-4 w-4" /> : <Pencil className="h-4 w-4" />}
                    </Button>
                </div>
            </div>

            {isEditing && (
                <Form {...form}>
                    <form
                        onSubmit={form.handleSubmit(saveTwilioCredentials)}
                        className="space-y-4 border-t border-border bg-background/60 px-4 py-5"
                    >
                        <div className="grid gap-4 sm:grid-cols-2">
                            <FormField
                                control={form.control}
                                name="twilio_account_sid"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Account SID</FormLabel>
                                        <FormControl>
                                            <Input
                                                autoComplete="off"
                                                placeholder={status?.twilio_account_sid_masked || "AC..."}
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="twilio_auth_token"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Auth Token</FormLabel>
                                        <FormControl>
                                            <Input
                                                type="password"
                                                autoComplete="new-password"
                                                placeholder="Enter auth token"
                                                {...field}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>
                        <div className="flex justify-end">
                            <Button type="submit" size="sm" disabled={isSaving}>
                                {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                {status?.twilio_configured ? "Update Account" : "Connect Account"}
                            </Button>
                        </div>
                    </form>
                </Form>
            )}
        </Card>
    );
}

export function TenantCredentialsForm({ institution, onUpdated }: InstitutionCredentialsFormProps) {
    const [editingSection, setEditingSection] = useState<SectionKey | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [isVerifying, setIsVerifying] = useState(false);
    const [verifiedKey, setVerifiedKey] = useState<string | null>(null);
    const [verification, setVerification] = useState<{
        ok: boolean;
        message: string;
        credential_mode: string;
        nexhealth_credential_mode?: "platform" | "institution";
        api_key_hash?: string | null;
    } | null>(null);

    const form = useForm<CredentialsFormValues>({
        resolver: zodResolver(credentialsSchema),
        defaultValues: {
            // Read the stored choice, not "does a key exist". A clinic can be on the
            // platform key with a stale key still on the row, and inferring would
            // show the wrong mode.
            credential_mode: institution.nexhealth_credential_mode ?? "platform",
            nexhealth_api_key: "",
        },
    });
    const credentialMode = form.watch("credential_mode");
    const clinicApiKey = form.watch("nexhealth_api_key")?.trim() || "";

    // Reset form when institution data is refreshed (e.g. after save)
    useEffect(() => {
        form.reset({
            // Read the stored choice, not "does a key exist". A clinic can be on the
            // platform key with a stale key still on the row, and inferring would
            // show the wrong mode.
            credential_mode: institution.nexhealth_credential_mode ?? "platform",
            nexhealth_api_key: "",
        });
        setVerifiedKey(null);
        setVerification(null);
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
            if (editingSection === "nexhealth") {
                // Send the mode explicitly — the backend no longer infers it from
                // whether a key is present, and refuses to fall back to the
                // platform key for an institution set to use its own.
                payload.nexhealth_credential_mode = values.credential_mode;
            }
            if (editingSection === "nexhealth" && values.credential_mode === "platform") {
                payload.nexhealth_api_key = null;
            }
            if (editingSection === "nexhealth" && values.credential_mode === "institution") {
                const key = values.nexhealth_api_key?.trim() || "";
                if (!institution.has_nexhealth_key && !key) {
                    toast.error("Enter and verify a clinic API key before saving");
                    return;
                }
                if (key && verifiedKey !== key) {
                    toast.error("Verify this clinic API key before saving");
                    return;
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
            setVerifiedKey(null);
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
        const apiKey = form.getValues("nexhealth_api_key")?.trim();
        if (credentialMode === "institution" && !institution.has_nexhealth_key && !apiKey) {
            toast.error("Enter an API key before verifying");
            return;
        }

        setIsVerifying(true);
        setVerifiedKey(null);
        setVerification(null);
        try {
            const { data } = await api.post(`/admin/institutions/${institution.slug}/nexhealth/verify`, {
                nexhealth_api_key: apiKey || undefined,
            });
            setVerification(data);
            if (data.ok) {
                setVerifiedKey(apiKey || "__stored_or_platform__");
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
            <Card className={`overflow-hidden transition-colors duration-150 hover:border-foreground/15 ${isEditing ? "ring-1 ring-primary/40 border-border" : ""}`}>
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
                                className="w-8 text-muted-foreground hover:bg-primary/10 hover:text-primary"
                                onClick={() => setEditingSection(null)}
                            >
                                <X className="h-4 w-4" />
                            </Button>
                        ) : (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="w-8 text-muted-foreground hover:bg-primary/10 hover:text-primary"
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
        <div className="space-y-3">
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
                                        setVerifiedKey(null);
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
                            {credentialMode === "institution" && (
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
                                                onChange={(event) => {
                                                    field.onChange(event);
                                                    setVerifiedKey(null);
                                                    setVerification(null);
                                                }}
                                                value={field.value}
                                                onBlur={field.onBlur}
                                                name={field.name}
                                                ref={field.ref}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            )}
                            <div className="flex flex-wrap items-center gap-3">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={handleVerify}
                                    disabled={isVerifying}
                                >
                                    {isVerifying ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : verification?.ok ? (
                                        <CheckCircle2 className="mr-2 h-4 w-4" />
                                    ) : (
                                        <AlertCircle className="mr-2 h-4 w-4" />
                                    )}
                                    {isVerifying ? "Verifying..." : "Verify Key"}
                                </Button>
                                {verification && (
                                    <span className={`text-xs font-medium ${verification.ok ? "text-green-600 dark:text-green-400" : "text-destructive"}`}>
                                        {verification.message}
                                    </span>
                                )}
                            </div>
                        </div>
                    </CredentialCard>

                    {editingSection && (
                        <div className="flex items-center justify-end pt-2">
                            <Button
                                type="submit"
                                disabled={
                                    isSaving ||
                                    (
                                        editingSection === "nexhealth" &&
                                        credentialMode === "institution" &&
                                        (
                                            (!institution.has_nexhealth_key && !clinicApiKey) ||
                                            (!!clinicApiKey && verifiedKey !== clinicApiKey)
                                        )
                                    )
                                }
                                size="sm"
                            >
                                {isSaving ? "Saving..." : "Save Changes"}
                            </Button>
                        </div>
                    )}
                </form>
            </Form>
            <TwilioCredentialsCard institutionSlug={institution.slug} />
        </div>
    );
}
