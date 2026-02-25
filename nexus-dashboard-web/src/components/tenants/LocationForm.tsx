import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import api from "@/lib/api";
import { listRetellAgents, type RetellAgent } from "@/lib/admin-api";
import type { Location, InstitutionBasicListResponse, InstitutionBasic } from "@/types";

const US_TIMEZONES = [
    { value: "America/Puerto_Rico", label: "Atlantic Time (Puerto Rico/Virgin Islands)" },
    { value: "America/New_York", label: "Eastern Time (ET)" },
    { value: "America/Chicago", label: "Central Time (CT)" },
    { value: "America/Denver", label: "Mountain Time (MT)" },
    { value: "America/Phoenix", label: "Mountain Time (Arizona - No DST)" },
    { value: "America/Los_Angeles", label: "Pacific Time (PT)" },
    { value: "America/Anchorage", label: "Alaska Time (AKT)" },
    { value: "Pacific/Honolulu", label: "Hawaii-Aleutian Time (HST)" },
    { value: "Pacific/Guam", label: "Chamorro Time (Guam)" },
];

const locationSchema = z.object({
    name: z.string().min(1, "Name is required"),
    slug: z.string().optional(),
    nexhealth_subdomain: z.string().optional(),
    nexhealth_location_id: z.string().optional(),
    retell_agent_id: z.string().optional(),
    address: z.string().optional(),
    city: z.string().optional(),
    state: z.string().optional(),
    phone: z.string().optional(),
    timezone: z.string().optional(),
});

type LocationFormValues = z.infer<typeof locationSchema>;

interface LocationFormProps {
    tenantSlug: string;
    location?: Location;
    onSuccess: () => void;
}

export function LocationForm({ tenantSlug, location, onSuccess }: LocationFormProps) {
    const isEditing = !!location;
    const [nexHealthInstitutions, setNexHealthInstitutions] = useState<InstitutionBasic[]>([]);
    const [isLoadingNH, setIsLoadingNH] = useState(false);

    // Retell Agents
    const [retellAgents, setRetellAgents] = useState<RetellAgent[]>([]);
    const [isLoadingAgents, setIsLoadingAgents] = useState(false);

    const form = useForm<LocationFormValues>({
        resolver: zodResolver(locationSchema),
        defaultValues: {
            name: location?.name || "",
            slug: location?.slug || "",
            nexhealth_subdomain: location?.nexhealth_subdomain || "",
            nexhealth_location_id: location?.nexhealth_location_id || "",
            retell_agent_id: location?.retell_agent_id || "",
            address: location?.address || "",
            city: location?.city || "",
            state: location?.state || "",
            phone: location?.phone || "",
            timezone: location?.timezone || "",
        },
    });

    // Fetch NexHealth institutions + locations on mount
    useEffect(() => {
        async function fetchNHLocations() {
            setIsLoadingNH(true);
            try {
                const { data } = await api.get<InstitutionBasicListResponse>("/admin/tenants/nexhealth/locations");
                setNexHealthInstitutions(data.data);
            } catch (error) {
                console.error("Failed to fetch NexHealth locations", error);
            } finally {
                setIsLoadingNH(false);
            }
        }
        fetchNHLocations();

        async function fetchAgents() {
            setIsLoadingAgents(true);
            try {
                const agents = await listRetellAgents();
                setRetellAgents(agents || []);
            } catch (error) {
                console.error("Failed to fetch Retell agents", error);
            } finally {
                setIsLoadingAgents(false);
            }
        }
        fetchAgents();
    }, []);

    // Flatten for dropdown display, but keep institution reference for subdomain lookup
    const nexHealthLocations = nexHealthInstitutions.flatMap(inst => inst.locations);

    function onLocationSelect(locationId: string) {
        const selected = nexHealthLocations.find(l => String(l.id) === locationId);
        if (!selected) return;

        // Auto-fill form
        form.setValue("nexhealth_location_id", String(selected.id));
        form.setValue("name", selected.name);
        // Generate slug from name
        const slug = selected.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
        form.setValue("slug", slug);

        if (selected.street_address) form.setValue("address", selected.street_address);
        if (selected.city) form.setValue("city", selected.city);
        if (selected.state) form.setValue("state", selected.state);
        if (selected.phone_number) form.setValue("phone", selected.phone_number);
        if (selected.tz) form.setValue("timezone", selected.tz);

        // Look up the parent institution to auto-fill subdomain
        const parentInstitution = nexHealthInstitutions.find(inst =>
            inst.locations.some(l => l.id === selected.id)
        );
        if (parentInstitution?.subdomain) {
            form.setValue("nexhealth_subdomain", parentInstitution.subdomain);
        }
    }

    async function onSubmit(values: LocationFormValues) {
        try {
            const payload: Record<string, unknown> = {};

            if (isEditing) {
                // PATCH: only send changed fields, skip slug
                const defaults = form.formState.defaultValues as Record<string, unknown>;
                for (const [key, val] of Object.entries(values)) {
                    if (key === "slug") continue;
                    if (val !== defaults[key]) {
                        // Send empty string as null to clear optional fields
                        payload[key] = val === "" ? null : val;
                    }
                }

                if (Object.keys(payload).length === 0) {
                    toast.info("No changes to save");
                    return;
                }

                await api.patch(`/admin/tenants/${tenantSlug}/locations/${location!.slug}`, payload);
                toast.success("Location updated");
            } else {
                // POST: validate slug is provided for create
                if (!values.slug) {
                    form.setError("slug", { message: "Slug is required" });
                    return;
                }
                if (!/^[a-z0-9-]+$/.test(values.slug)) {
                    form.setError("slug", { message: "Slug must be lowercase alphanumeric with hyphens" });
                    return;
                }
                // Strip empty optional strings
                for (const [key, val] of Object.entries(values)) {
                    if (val !== "" && val !== undefined) {
                        payload[key] = val;
                    }
                }
                await api.post(`/admin/tenants/${tenantSlug}/locations`, payload);
                toast.success("Location created");
            }

            onSuccess();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || `Failed to ${isEditing ? "update" : "create"} location`);
        }
    }

    return (
        <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">

                <FormField
                    control={form.control}
                    name="nexhealth_location_id"
                    render={({ field }) => (
                        <FormItem className="space-y-2">
                            <FormLabel>Select NexHealth Location (Optional)</FormLabel>
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
                                        <SelectValue placeholder={isLoadingNH ? "Loading locations..." : "Choose a location from NexHealth"} />
                                    </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                    <SelectItem value="none">None / Manual Entry</SelectItem>
                                    {nexHealthLocations.map((loc) => (
                                        <SelectItem key={loc.id} value={String(loc.id)}>
                                            {loc.name} (ID: {loc.id})
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <FormMessage />
                            <p className="text-sm text-muted-foreground">
                                Selecting a location will auto-fill the form fields below.
                            </p>
                        </FormItem>
                    )}
                />

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
                                <FormLabel>Slug</FormLabel>
                                <FormControl>
                                    <Input placeholder="main-office" {...field} />
                                </FormControl>
                                <FormMessage />
                            </FormItem>
                        )}
                    />
                )}

                <div className="border-t pt-4">
                    <p className="text-sm font-medium mb-3">NexHealth Settings</p>
                    <div className="space-y-4">
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
                    </div>
                </div>

                <div className="border-t pt-4">
                    <p className="text-sm font-medium mb-3">Retell AI Settings</p>
                    <div className="space-y-4">
                        <FormField
                            control={form.control}
                            name="retell_agent_id"
                            render={({ field }) => (
                                <FormItem>
                                    <FormLabel>Agent ID</FormLabel>
                                    <Select
                                        onValueChange={field.onChange}
                                        value={field.value || ""}
                                        disabled={isLoadingAgents}
                                    >
                                        <FormControl>
                                            <SelectTrigger>
                                                <SelectValue placeholder={isLoadingAgents ? "Loading agents..." : "Select a Retell Agent"} />
                                            </SelectTrigger>
                                        </FormControl>
                                        <SelectContent>
                                            <SelectItem value="none">None</SelectItem>
                                            {retellAgents.map((agent) => (
                                                <SelectItem key={agent.agent_id} value={agent.agent_id}>
                                                    {agent.agent_id}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <FormMessage />
                                </FormItem>
                            )}
                        />
                    </div>
                </div>


                <div className="border-t pt-4">
                    <p className="text-sm font-medium mb-3">Address</p>
                    <div className="space-y-4">
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
                        <div className="grid grid-cols-2 gap-4">
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
                            <FormField
                                control={form.control}
                                name="state"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>State</FormLabel>
                                        <FormControl>
                                            <Input placeholder="CA" {...field} />
                                        </FormControl>
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
                                            <Input placeholder="(555) 123-4567" {...field} />
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
                                                    <SelectValue placeholder="Select a timezone" />
                                                </SelectTrigger>
                                            </FormControl>
                                            <SelectContent>
                                                {US_TIMEZONES.map((tz) => (
                                                    <SelectItem key={tz.value} value={tz.value}>
                                                        {tz.label} ({tz.value})
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                        </div>
                    </div>
                </div>

                <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
                    {form.formState.isSubmitting
                        ? (isEditing ? "Updating..." : "Creating...")
                        : (isEditing ? "Update Location" : "Create Location")}
                </Button>
            </form>
        </Form>
    );
}
