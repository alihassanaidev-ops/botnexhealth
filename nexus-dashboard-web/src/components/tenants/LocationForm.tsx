import { useState, useEffect } from "react";
import { Loader2, CheckCircle2, XCircle, HelpCircle, RefreshCw } from "lucide-react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/foundation/compat/button";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/foundation/compat/form";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/foundation/compat/select";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/foundation/compat/tooltip";
import { Input } from "@/components/foundation/compat/input";
import { Badge } from "@/components/foundation/compat/badge";
import { toast } from "sonner";
import api from "@/lib/api";
import { verifyRetellAgent, listInstitutionTwilioPhoneNumbers } from "@/lib/admin-api";
import { SUPPORTED_TIMEZONES } from "@/lib/timezones";
import type { Location, InstitutionBasicListResponse, InstitutionBasic, TwilioPhoneNumber } from "@/types";
import { cn } from "@/lib/utils";
import { OutboundVoiceProfilesAdmin } from "./OutboundVoiceProfilesAdmin";
import { RetellSmsProfilesAdmin } from "./RetellSmsProfilesAdmin";

const US_STATES = [
    { value: "AL", label: "AL — Alabama" }, { value: "AK", label: "AK — Alaska" },
    { value: "AZ", label: "AZ — Arizona" }, { value: "AR", label: "AR — Arkansas" },
    { value: "CA", label: "CA — California" }, { value: "CO", label: "CO — Colorado" },
    { value: "CT", label: "CT — Connecticut" }, { value: "DE", label: "DE — Delaware" },
    { value: "FL", label: "FL — Florida" }, { value: "GA", label: "GA — Georgia" },
    { value: "HI", label: "HI — Hawaii" }, { value: "ID", label: "ID — Idaho" },
    { value: "IL", label: "IL — Illinois" }, { value: "IN", label: "IN — Indiana" },
    { value: "IA", label: "IA — Iowa" }, { value: "KS", label: "KS — Kansas" },
    { value: "KY", label: "KY — Kentucky" }, { value: "LA", label: "LA — Louisiana" },
    { value: "ME", label: "ME — Maine" }, { value: "MD", label: "MD — Maryland" },
    { value: "MA", label: "MA — Massachusetts" }, { value: "MI", label: "MI — Michigan" },
    { value: "MN", label: "MN — Minnesota" }, { value: "MS", label: "MS — Mississippi" },
    { value: "MO", label: "MO — Missouri" }, { value: "MT", label: "MT — Montana" },
    { value: "NE", label: "NE — Nebraska" }, { value: "NV", label: "NV — Nevada" },
    { value: "NH", label: "NH — New Hampshire" }, { value: "NJ", label: "NJ — New Jersey" },
    { value: "NM", label: "NM — New Mexico" }, { value: "NY", label: "NY — New York" },
    { value: "NC", label: "NC — North Carolina" }, { value: "ND", label: "ND — North Dakota" },
    { value: "OH", label: "OH — Ohio" }, { value: "OK", label: "OK — Oklahoma" },
    { value: "OR", label: "OR — Oregon" }, { value: "PA", label: "PA — Pennsylvania" },
    { value: "RI", label: "RI — Rhode Island" }, { value: "SC", label: "SC — South Carolina" },
    { value: "SD", label: "SD — South Dakota" }, { value: "TN", label: "TN — Tennessee" },
    { value: "TX", label: "TX — Texas" }, { value: "UT", label: "UT — Utah" },
    { value: "VT", label: "VT — Vermont" }, { value: "VA", label: "VA — Virginia" },
    { value: "WA", label: "WA — Washington" }, { value: "WV", label: "WV — West Virginia" },
    { value: "WI", label: "WI — Wisconsin" }, { value: "WY", label: "WY — Wyoming" },
    { value: "DC", label: "DC — Washington D.C." }, { value: "PR", label: "PR — Puerto Rico" },
    { value: "GU", label: "GU — Guam" },
];

const locationSchema = z.object({
    name: z.string().min(1, "Name is required"),
    slug: z.string().optional(),
    nexhealth_subdomain: z.string().optional(),
    nexhealth_location_id: z.string().optional(),
    gotracker_base_url: z.string().optional(),
    gotracker_product_key: z.string().optional(),
    gotracker_webhook_subscription_id: z.string().optional(),
    gotracker_webhook_secret: z.string().optional(),
    retell_agent_id: z.string().optional(),
    twilio_from_number: z.string().optional(),
    address: z.string().optional(),
    city: z.string().optional(),
    state: z.string().optional(),
    phone: z.string().optional(),
    timezone: z.string().optional(),
});

type LocationFormValues = z.infer<typeof locationSchema>;

interface LocationFormProps {
    institutionSlug: string;
    location?: Location;
    /** False for call-intelligence-only tenants — hides PMS fields. */
    hasPms?: boolean;
    pmsType?: string | null;
    onSuccess: () => void;
    onCancel: () => void;
}

function formatPhone(raw: string): string {
    const digits = raw.replace(/\D/g, "").slice(0, 10);
    if (digits.length <= 3) return digits;
    if (digits.length <= 6) return `(${digits.slice(0, 3)}) ${digits.slice(3)}`;
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}

function SectionCard({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
    return (
        <div className="space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm">
            <div className="space-y-0.5">
                <h3 className="text-base font-semibold leading-none tracking-tight">{title}</h3>
                {description && <p className="text-sm text-muted-foreground">{description}</p>}
            </div>
            <div className="space-y-4">{children}</div>
        </div>
    );
}

function FieldHint({ text }: { text: string }) {
    return (
        <TooltipProvider delayDuration={200}>
            <Tooltip>
                <TooltipTrigger asChild>
                    <HelpCircle className="inline h-3.5 w-3.5 ml-1.5 mb-0.5 text-muted-foreground hover:text-muted-foreground cursor-help" />
                </TooltipTrigger>
                <TooltipContent side="right" className="max-w-xs text-xs">{text}</TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}

function GoTrackerWebhookStatus({ location }: { location?: Location }) {
    if (!location) return null;
    const hasApiKey = location.has_gotracker_product_key;
    const status = location.gotracker_webhook_status;
    const connected = hasApiKey && status === "active" && !!location.gotracker_webhook_subscription_id && location.has_gotracker_webhook_secret;
    const failed = status === "failed";
    const message = !hasApiKey
        ? "Paste the GoTracker API key and save to connect this location."
        : connected
            ? "GoTracker API and webhooks are connected."
            : failed
                ? "GoTracker API key is saved, but webhook setup failed. Check the subscription status or use advanced adoption below."
                : "GoTracker API key is saved. Webhook setup is pending or waiting for reconciliation.";

    return (
        <div className={cn(
            "rounded-lg border p-3 text-sm",
            connected
                ? "border-green-500/25 bg-green-500/10 text-green-700 dark:text-green-300"
                : failed
                    ? "border-destructive/30 bg-destructive/10 text-destructive"
                    : "border-border bg-background/70 text-muted-foreground",
        )}>
            <div className="flex items-center justify-between gap-3">
                <span>{message}</span>
                <Badge variant="secondary" className={cn(
                    "shrink-0 capitalize",
                    connected && "bg-green-500/15 text-green-700 dark:text-green-300",
                    failed && "bg-destructive/15 text-destructive",
                )}>
                    {connected ? "Connected" : status || "Not connected"}
                </Badge>
            </div>
            {location.gotracker_webhook_subscription_id && (
                <p className="mt-1 text-xs opacity-80">
                    Subscription ID: {location.gotracker_webhook_subscription_id}
                </p>
            )}
        </div>
    );
}

export function LocationForm({ institutionSlug, location, hasPms = true, pmsType = "nexhealth", onSuccess, onCancel }: LocationFormProps) {
    const isEditing = !!location;
    const isNexHealth = hasPms && (pmsType ?? "nexhealth") === "nexhealth";
    const isGoTracker = hasPms && pmsType === "gotracker";
    const [nexHealthInstitutions, setNexHealthInstitutions] = useState<InstitutionBasic[]>([]);
    const [isLoadingNH, setIsLoadingNH] = useState(false);
    const [isVerifyingAgent, setIsVerifyingAgent] = useState(false);
    const [agentVerificationStatus, setAgentVerificationStatus] = useState<"idle" | "success" | "error">("idle");
    const [twilioNumbers, setTwilioNumbers] = useState<TwilioPhoneNumber[]>([]);
    const [isLoadingTwilio, setIsLoadingTwilio] = useState(false);
    const [twilioLoadError, setTwilioLoadError] = useState<string | null>(null);
    const [isConnectingTwilio, setIsConnectingTwilio] = useState(false);
    const [isConnectingGoTracker, setIsConnectingGoTracker] = useState(false);

    const form = useForm<LocationFormValues>({
        resolver: zodResolver(locationSchema),
        defaultValues: {
            name: location?.name || "",
            slug: location?.slug || "",
            nexhealth_subdomain: location?.nexhealth_subdomain || "",
            nexhealth_location_id: location?.nexhealth_location_id || "",
            gotracker_base_url: location?.gotracker_base_url || "",
            gotracker_product_key: "",
            gotracker_webhook_subscription_id: location?.gotracker_webhook_subscription_id || "",
            gotracker_webhook_secret: "",
            retell_agent_id: location?.retell_agent_id || "",
            twilio_from_number: location?.twilio_from_number || "",
            address: location?.address || "",
            city: location?.city || "",
            state: location?.state || "",
            phone: location?.phone || "",
            timezone: location?.timezone || "",
        },
    });

    const isDirty = form.formState.isDirty;

    // Fetch NexHealth institutions + locations on mount
    useEffect(() => {
        if (!isNexHealth) return;
        async function fetchNHLocations() {
            setIsLoadingNH(true);
            try {
                const { data } = await api.get<InstitutionBasicListResponse>(
                    `/admin/institutions/${institutionSlug}/nexhealth/locations`
                );
                setNexHealthInstitutions(data.data);
            } catch {
                // Silently fail — NexHealth locations are optional hints
            } finally {
                setIsLoadingNH(false);
            }
        }
        fetchNHLocations();
    }, [isNexHealth, institutionSlug]);

    // Fetch Twilio phone numbers on mount
    useEffect(() => {
        async function fetchTwilioNumbers() {
            setIsLoadingTwilio(true);
            setTwilioLoadError(null);
            try {
                const numbers = await listInstitutionTwilioPhoneNumbers(institutionSlug);
                setTwilioNumbers(numbers.filter(n => n.capabilities.sms));
            } catch (err: unknown) {
                const error = err as { response?: { data?: { detail?: string } } };
                setTwilioLoadError(
                    error.response?.data?.detail || "Unable to load this institution's Twilio numbers",
                );
            } finally {
                setIsLoadingTwilio(false);
            }
        }
        fetchTwilioNumbers();
    }, [institutionSlug]);

    const nexHealthLocations = nexHealthInstitutions.flatMap(inst => inst.locations);

    function onLocationSelect(locationId: string) {
        const selected = nexHealthLocations.find(l => String(l.id) === locationId);
        if (!selected) return;

        form.setValue("nexhealth_location_id", String(selected.id), { shouldDirty: true });
        form.setValue("name", selected.name, { shouldDirty: true });
        const slug = selected.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        form.setValue("slug", slug, { shouldDirty: true });

        if (selected.street_address) form.setValue("address", selected.street_address, { shouldDirty: true });
        if (selected.city) form.setValue("city", selected.city, { shouldDirty: true });
        if (selected.state) form.setValue("state", selected.state, { shouldDirty: true });
        if (selected.phone_number) form.setValue("phone", formatPhone(selected.phone_number), { shouldDirty: true });
        if (selected.tz) form.setValue("timezone", selected.tz, { shouldDirty: true });

        const parentInstitution = nexHealthInstitutions.find(inst =>
            inst.locations.some(l => l.id === selected.id)
        );
        if (parentInstitution?.subdomain) {
            form.setValue("nexhealth_subdomain", parentInstitution.subdomain, { shouldDirty: true });
        }
    }

    async function onSubmit(values: LocationFormValues) {
        try {
            const payload: Record<string, unknown> = {};

            if (isEditing) {
                const defaults = form.formState.defaultValues as Record<string, unknown>;
                for (const [key, val] of Object.entries(values)) {
                    if (key === "slug") continue;
                    if (val !== defaults[key]) {
                        payload[key] = val === "" ? null : val;
                    }
                }

                if (Object.keys(payload).length === 0) {
                    toast.info("No changes to save");
                    return;
                }

                await api.patch(`/admin/institutions/${institutionSlug}/locations/${location!.slug}`, payload);
                toast.success("Location updated successfully");
            } else {
                if (!values.slug) {
                    form.setError("slug", { message: "Slug is required" });
                    return;
                }
                if (!/^[a-z0-9-]+$/.test(values.slug)) {
                    form.setError("slug", { message: "Slug must be lowercase alphanumeric with hyphens" });
                    return;
                }
                for (const [key, val] of Object.entries(values)) {
                    if (val !== "" && val !== undefined) {
                        payload[key] = val;
                    }
                }
                await api.post(`/admin/institutions/${institutionSlug}/locations`, payload);
                toast.success("Location created successfully");
            }

            onSuccess();
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || `Failed to ${isEditing ? "update" : "create"} location`);
        }
    }

    async function reconnectTwilioWebhook() {
        if (!location?.twilio_from_number) return;
        setIsConnectingTwilio(true);
        try {
            await api.post(
                `/admin/institutions/${institutionSlug}/locations/${location.slug}/twilio/webhook`,
            );
            toast.success("Twilio inbound SMS webhook connected");
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error.response?.data?.detail || "Failed to connect Twilio webhook");
        } finally {
            setIsConnectingTwilio(false);
        }
    }

    async function reconnectGoTrackerWebhook() {
        if (!location?.has_gotracker_product_key) return;
        setIsConnectingGoTracker(true);
        try {
            const { data } = await api.post<{ action: "created" | "rotated" }>(
                `/admin/institutions/${institutionSlug}/locations/${location.slug}/gotracker/webhook/reconnect`,
            );
            toast.success(
                data.action === "rotated"
                    ? "GoTracker webhook signing secret rotated"
                    : "GoTracker webhook subscription created",
            );
            onSuccess();
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error.response?.data?.detail || "Failed to reconnect GoTracker webhook");
        } finally {
            setIsConnectingGoTracker(false);
        }
    }

    return (
        <TooltipProvider>
            <Form {...form}>
                <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5 pb-24">

                    {/* NexHealth Autofill Picker */}
                    {isNexHealth && (
                    <FormField
                        control={form.control}
                        name="nexhealth_location_id"
                        render={({ field }) => (
                            <FormItem className="space-y-2">
                                <FormLabel>
                                    Import from NexHealth
                                    <FieldHint text="Selecting a NexHealth location will auto-fill all fields below. You can still edit them manually." />
                                </FormLabel>
                                <Select
                                    onValueChange={(val) => {
                                        const newValue = val === "none" ? "" : val;
                                        field.onChange(newValue);
                                        if (val !== "none") {
                                            onLocationSelect(val);
                                        }
                                    }}
                                    value={field.value || "none"}
                                    disabled={isLoadingNH}
                                >
                                    <FormControl>
                                        <SelectTrigger>
                                            <SelectValue placeholder={isLoadingNH ? "Loading locations…" : "Select a NexHealth location to auto-fill"} />
                                        </SelectTrigger>
                                    </FormControl>
                                    <SelectContent>
                                        <SelectItem value="none">None — manual entry</SelectItem>
                                        {nexHealthLocations.map((loc) => (
                                            <SelectItem key={loc.id} value={String(loc.id)}>
                                                {loc.name} (ID: {loc.id})
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                    )}

                    {/* Section: Location Info */}
                    <SectionCard title="Location Info" description="Core details about this location.">
                        <FormField
                            control={form.control}
                            name="name"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>Location Name</FormLabel>
                                    <FormControl>
                                        <Input placeholder="Main Office" {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        {!isEditing && (
                            <FormField
                                control={form.control}
                                name="slug"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>
                                            Slug
                                            <FieldHint text="URL-safe identifier (e.g. main-office). Auto-generated from name. Cannot be changed after creation." />
                                        </FormLabel>
                                        <FormControl>
                                            <Input placeholder="main-office" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        )}
                    </SectionCard>

                    {/* Section: NexHealth Integration */}
                    {isNexHealth && (
                    <SectionCard title="NexHealth Integration" description="Connect this location to your NexHealth PMS account.">
                        <FormField
                            control={form.control}
                            name="nexhealth_subdomain"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>
                                        Subdomain
                                        <FieldHint text="Found in your NexHealth admin URL, e.g. your-practice.nexhealth.com → your-practice" />
                                    </FormLabel>
                                    <FormControl>
                                        <Input placeholder="acme-dental" {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </SectionCard>
                    )}

                    {/* Section: GoTracker Integration */}
                    {isGoTracker && (
                    <SectionCard title="GoTracker Integration" description="Connect this location to the ScaleNexus GoTracker Synchronizer.">
                        <GoTrackerWebhookStatus location={location} />
                        {isEditing && location?.has_gotracker_product_key && (
                            <div className="flex justify-end">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="gap-1.5"
                                    onClick={reconnectGoTrackerWebhook}
                                    disabled={isConnectingGoTracker || isDirty}
                                    title={isDirty ? "Save location changes before reconnecting" : undefined}
                                >
                                    <RefreshCw className={cn(
                                        "h-4 w-4",
                                        isConnectingGoTracker && "animate-spin",
                                    )} />
                                    Reconnect webhook
                                </Button>
                            </div>
                        )}
                        <FormField
                            control={form.control}
                            name="gotracker_base_url"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>
                                        Synchronizer Base URL
                                        <FieldHint text="Defaults to https://synchronizer.scalenexus.ai when left blank." />
                                    </FormLabel>
                                    <FormControl>
                                        <Input placeholder="https://synchronizer.scalenexus.ai" {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <FormField
                            control={form.control}
                            name="gotracker_product_key"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>
                                        GoTracker API Key
                                        <FieldHint text="API key from the GoTracker Synchronizer admin panel. It must include the scopes needed for read, book, and webhooks. Stored encrypted and never shown again." />
                                    </FormLabel>
                                    <FormControl>
                                        <Input
                                            type="password"
                                            placeholder={location?.has_gotracker_product_key ? "Configured — enter a new key to replace" : "Paste GoTracker API key"}
                                            autoComplete="off"
                                            {...field}
                                        />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <p className="text-xs text-muted-foreground">
                            Saving the API key automatically creates or updates this location's webhook subscription when the callback base URL is configured.
                        </p>
                        {isEditing && (
                            <div className="space-y-3">
                                <div>
                                    <h4 className="text-sm font-medium">Advanced webhook adoption</h4>
                                    <p className="text-xs text-muted-foreground">
                                        Leave these blank for normal setup. Use them only to adopt an existing Synchronizer webhook subscription.
                                    </p>
                                </div>
                                <div className="grid gap-4 md:grid-cols-2">
                                <FormField
                                    control={form.control}
                                    name="gotracker_webhook_subscription_id"
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>
                                                Webhook Subscription ID
                                                <FieldHint text="Synchronizer subscription id for this location. Use this to adopt an existing cloud subscription instead of creating a new one." />
                                            </FormLabel>
                                            <FormControl>
                                                <Input placeholder="14" {...field} />
                                            </FormControl>
                                            {location?.gotracker_webhook_status && (
                                                <p className="text-xs text-muted-foreground">
                                                    Current status: {location.gotracker_webhook_status}
                                                </p>
                                            )}
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                                <FormField
                                    control={form.control}
                                    name="gotracker_webhook_secret"
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>
                                                Webhook Signing Secret
                                                <FieldHint text="Paste the secret returned by the Synchronizer for this location's webhook subscription. Stored encrypted and never shown again." />
                                            </FormLabel>
                                            <FormControl>
                                                <Input
                                                    type="password"
                                                    placeholder={location?.has_gotracker_webhook_secret ? "Configured — enter a new secret to replace" : "Paste webhook secret"}
                                                    autoComplete="off"
                                                    {...field}
                                                />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                                </div>
                            </div>
                        )}
                    </SectionCard>
                    )}

                    {/* Section: Retell AI Integration */}
                    <SectionCard title="Retell AI Integration" description="Link the voice agent assigned to this location.">
                        <FormField
                            control={form.control}
                            name="retell_agent_id"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>
                                        Agent ID
                                        <FieldHint text="Found in Retell AI dashboard → Agents. Looks like agent_xxxxxxxxxxxxxxxx" />
                                    </FormLabel>
                                    <div className="flex items-center gap-2">
                                        <FormControl>
                                            <Input
                                                placeholder="agent_xxx"
                                                {...field}
                                                disabled={isVerifyingAgent}
                                                className={cn(
                                                    "transition-all",
                                                    agentVerificationStatus === "success" && "ring-2 ring-green-500/50 border-green-500/50",
                                                    agentVerificationStatus === "error" && "ring-2 ring-destructive/50 border-destructive/50"
                                                )}
                                                onChange={(e) => {
                                                    field.onChange(e);
                                                    setAgentVerificationStatus("idle");
                                                }}
                                            />
                                        </FormControl>
                                        <Button
                                            type="button"
                                            variant="secondary"
                                            size="sm"
                                            className="shrink-0"
                                            disabled={!field.value || isVerifyingAgent}
                                            onClick={async () => {
                                                setIsVerifyingAgent(true);
                                                setAgentVerificationStatus("idle");
                                                try {
                                                    await verifyRetellAgent(field.value || "");
                                                    setAgentVerificationStatus("success");
                                                } catch {
                                                    setAgentVerificationStatus("error");
                                                } finally {
                                                    setIsVerifyingAgent(false);
                                                }
                                            }}
                                        >
                                            {isVerifyingAgent
                                                ? <><Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />Verifying…</>
                                                : "Verify"}
                                        </Button>
                                    </div>
                                    {agentVerificationStatus === "success" && (
                                        <p className="text-sm font-medium text-green-600 dark:text-green-400 flex items-center gap-1.5 mt-1.5">
                                            <CheckCircle2 className="h-4 w-4 shrink-0" />
                                            Agent verified — this ID is active in Retell
                                        </p>
                                    )}
                                    {agentVerificationStatus === "error" && (
                                        <p className="text-sm font-medium text-destructive flex items-center gap-1.5 mt-1.5">
                                            <XCircle className="h-4 w-4 shrink-0" />
                                            Agent not found — check the ID and try again
                                        </p>
                                    )}
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </SectionCard>

                    {isEditing && location && (
                        <SectionCard
                            title="Outbound Voice Profiles"
                            description="Named outbound agents used by campaign workflow voice steps."
                        >
                            <OutboundVoiceProfilesAdmin
                                institutionSlug={institutionSlug}
                                locationSlug={location.slug}
                            />
                        </SectionCard>
                    )}

                    {isEditing && location && (
                        <SectionCard
                            title="Retell SMS Profiles"
                            description="Response-generator agents available to Retell SMS Conversation workflow nodes."
                        >
                            <RetellSmsProfilesAdmin locationId={location.id} />
                        </SectionCard>
                    )}

                    {/* Section: Twilio SMS */}
                    <SectionCard
                        title="Twilio SMS"
                        description="Select this location's number for workflow messages and patient replies."
                    >
                        <FormField
                            control={form.control}
                            name="twilio_from_number"
                            render={({ field }) => {
                                const options = field.value && !twilioNumbers.some(
                                    number => number.phone_number === field.value,
                                )
                                    ? [{
                                        sid: `assigned-${field.value}`,
                                        phone_number: field.value,
                                        friendly_name: "Currently assigned",
                                        capabilities: { voice: false, sms: true, mms: false },
                                        status: "active",
                                    }, ...twilioNumbers]
                                    : twilioNumbers;

                                return (
                                    <FormItem>
                                        <FormLabel>
                                            Outbound SMS Number
                                            <FieldHint text="Saving connects this number to the deployment's inbound SMS webhook." />
                                        </FormLabel>
                                        <div className="flex items-center gap-2">
                                            <Select
                                                onValueChange={(val) => field.onChange(val === "none" ? "" : val)}
                                                value={field.value || "none"}
                                                disabled={isLoadingTwilio}
                                            >
                                                <FormControl>
                                                    <SelectTrigger className="flex-1">
                                                        <SelectValue
                                                            placeholder={
                                                                isLoadingTwilio
                                                                    ? "Loading numbers…"
                                                                    : options.length === 0
                                                                        ? "No SMS-capable numbers found"
                                                                        : "Select a Twilio number"
                                                            }
                                                        />
                                                    </SelectTrigger>
                                                </FormControl>
                                                <SelectContent>
                                                    <SelectItem value="none">None — disable auto-SMS</SelectItem>
                                                    {options.map((n) => (
                                                        <SelectItem key={n.sid} value={n.phone_number}>
                                                            {n.phone_number}
                                                            {n.friendly_name ? ` — ${n.friendly_name}` : ""}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                            {isEditing && location.twilio_from_number && (
                                                <Tooltip>
                                                    <TooltipTrigger asChild>
                                                        <Button
                                                            type="button"
                                                            variant="outline"
                                                            size="icon"
                                                            className="shrink-0"
                                                            onClick={reconnectTwilioWebhook}
                                                            disabled={
                                                                isConnectingTwilio
                                                                || field.value !== location.twilio_from_number
                                                            }
                                                        >
                                                            <RefreshCw className={cn(
                                                                "h-4 w-4",
                                                                isConnectingTwilio && "animate-spin",
                                                            )} />
                                                            <span className="sr-only">Reconnect inbound SMS webhook</span>
                                                        </Button>
                                                    </TooltipTrigger>
                                                    <TooltipContent>Reconnect inbound SMS webhook</TooltipContent>
                                                </Tooltip>
                                            )}
                                        </div>
                                        {twilioLoadError && (
                                            <p className="text-sm text-destructive">{twilioLoadError}</p>
                                        )}
                                        <FormMessage />
                                    </FormItem>
                                );
                            }}
                        />
                    </SectionCard>

                    {/* Section: Address & Contact */}
                    <SectionCard title="Address & Contact" description="Physical location and contact information.">
                        <FormField
                            control={form.control}
                            name="address"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>Street Address</FormLabel>
                                    <FormControl>
                                        <Input placeholder="123 Main St" {...field} />
                                    </FormControl>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                        <div className="grid grid-cols-3 gap-4">
                            <div className="col-span-2">
                                <FormField
                                    control={form.control}
                                    name="city"
                                    render={({ field }) => (
                                        <FormItem>
                                            <FormLabel>City</FormLabel>
                                            <FormControl>
                                                <Input placeholder="San Francisco" {...field} />
                                            </FormControl>
                                            <FormMessage />
                                        </FormItem>
                                    )}
                                />
                            </div>
                            <FormField
                                control={form.control}
                                name="state"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>State</FormLabel>
                                        <Select onValueChange={field.onChange} value={field.value || ""}>
                                            <FormControl>
                                                <SelectTrigger>
                                                    <SelectValue placeholder="State" />
                                                </SelectTrigger>
                                            </FormControl>
                                            <SelectContent className="max-h-60">
                                                {US_STATES.map((s) => (
                                                    <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <FormField
                                control={form.control}
                                name="phone"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Phone</FormLabel>
                                        <FormControl>
                                            <Input
                                                placeholder="(555) 123-4567"
                                                {...field}
                                                onChange={(e) => {
                                                    field.onChange(formatPhone(e.target.value));
                                                }}
                                            />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="timezone"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Timezone</FormLabel>
                                        <Select onValueChange={field.onChange} value={field.value || ""}>
                                            <FormControl>
                                                <SelectTrigger>
                                                    <SelectValue placeholder="Select timezone" />
                                                </SelectTrigger>
                                            </FormControl>
                                            <SelectContent>
                                                {SUPPORTED_TIMEZONES.map((tz) => (
                                                    <SelectItem key={tz.value} value={tz.value}>
                                                        {tz.label}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>
                    </SectionCard>

                </form>

                {/* Sticky Footer */}
                <div className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/85">
                    <div className="px-6 py-3 flex items-center justify-between gap-4 w-full">
                        <div className="flex items-center gap-2">
                            {isDirty && (
                                <Badge variant="secondary" className="text-xs font-normal text-muted-foreground">
                                    Unsaved changes
                                </Badge>
                            )}
                        </div>
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={onCancel}
                                disabled={form.formState.isSubmitting}
                            >
                                Cancel
                            </Button>
                            <Button
                                type="submit"
                                size="sm"
                                disabled={form.formState.isSubmitting}
                                onClick={form.handleSubmit(onSubmit)}
                            >
                                {form.formState.isSubmitting
                                    ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{isEditing ? "Updating…" : "Creating…"}</>
                                    : isEditing ? "Save Changes" : "Create Location"}
                            </Button>
                        </div>
                    </div>
                </div>
            </Form>
        </TooltipProvider>
    );
}
