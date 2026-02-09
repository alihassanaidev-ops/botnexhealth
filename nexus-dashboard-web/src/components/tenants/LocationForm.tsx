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
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import api from "@/lib/api";
import type { Location } from "@/types";

const locationSchema = z.object({
    name: z.string().min(1, "Name is required"),
    slug: z.string().optional(),
    nexhealth_subdomain: z.string().optional(),
    nexhealth_location_id: z.string().optional(),
    retell_agent_id: z.string().optional(),
    retell_api_secret: z.string().optional(),
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

    const form = useForm<LocationFormValues>({
        resolver: zodResolver(locationSchema),
        defaultValues: {
            name: location?.name || "",
            slug: location?.slug || "",
            nexhealth_subdomain: location?.nexhealth_subdomain || "",
            nexhealth_location_id: location?.nexhealth_location_id || "",
            retell_agent_id: location?.retell_agent_id || "",
            retell_api_secret: "",
            address: location?.address || "",
            city: location?.city || "",
            state: location?.state || "",
            phone: location?.phone || "",
            timezone: location?.timezone || "",
        },
    });

    async function onSubmit(values: LocationFormValues) {
        try {
            const payload: Record<string, unknown> = {};

            if (isEditing) {
                // PATCH: only send changed fields, skip slug
                const defaults = form.formState.defaultValues as Record<string, unknown>;
                for (const [key, val] of Object.entries(values)) {
                    if (key === "slug") continue;
                    if (val !== defaults[key] && val !== "") {
                        payload[key] = val;
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
                                            placeholder={location?.has_retell_secret ? "••••••••  (leave blank to keep)" : "Enter API secret"}
                                            {...field}
                                        />
                                    </FormControl>
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
                                        <FormControl>
                                            <Input placeholder="America/Los_Angeles" {...field} />
                                        </FormControl>
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
