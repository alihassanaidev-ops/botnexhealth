import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Button } from "@/components/ui/button"
import {
    Form,
    FormControl,
    FormField,
    FormItem,
    FormLabel,
    FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { toast } from "sonner"
import api from "@/lib/api"

const institutionSchema = z.object({
    name: z.string().min(2, { message: "Name must be at least 2 characters." }),
    slug: z.string().min(2, { message: "Slug is required." }).regex(/^[a-z0-9-]+$/, { message: "Slug must be lowercase alphanumeric with hyphens." }),
    email: z.string().email({ message: "Invalid email address." }),
    pms_type: z.enum(["nexhealth", "gotracker", "none"]),
})

interface InstitutionFormProps {
    onSuccess: () => void
}

export function TenantForm({ onSuccess }: InstitutionFormProps) {
    const form = useForm<z.infer<typeof institutionSchema>>({
        resolver: zodResolver(institutionSchema),
        defaultValues: {
            name: "",
            slug: "",
            email: "",
            pms_type: "nexhealth",
        },
    })

    async function onSubmit(values: z.infer<typeof institutionSchema>) {
        try {
            await api.post("/admin/institutions", values)
            toast.success("Institution created successfully")
            form.reset()
            onSuccess()
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to create institution")
        }
    }

    return (
        <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4 rounded-xl border border-border bg-background/70 p-4">
                <FormField
                    control={form.control}
                    name="name"
                    render={({ field }) => (
                        <FormItem>
                            <FormLabel>Institution Name</FormLabel>
                            <FormControl>
                                <Input placeholder="Acme Dental" {...field} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
                <FormField
                    control={form.control}
                    name="slug"
                    render={({ field }) => (
                        <FormItem>
                            <FormLabel>Slug (Unique ID)</FormLabel>
                            <FormControl>
                                <Input placeholder="acme-dental" {...field} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
                <FormField
                    control={form.control}
                    name="email"
                    render={({ field }) => (
                        <FormItem>
                            <FormLabel>Admin Email</FormLabel>
                            <FormControl>
                                <Input placeholder="admin@acmedental.com" {...field} />
                            </FormControl>
                            <FormMessage />
                        </FormItem>
                    )}
                />
                <FormField
                    control={form.control}
                    name="pms_type"
                    render={({ field }) => (
                        <FormItem>
                            <FormLabel>PMS Integration</FormLabel>
                            <Select value={field.value} onValueChange={field.onChange}>
                                <FormControl>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                    <SelectItem value="nexhealth">NexHealth (syncs providers, appointment types, booking)</SelectItem>
                                    <SelectItem value="gotracker">GoTracker (via ScaleNexus Synchronizer)</SelectItem>
                                    <SelectItem value="none">None — call intelligence only (no booking/sync)</SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                Choose “None” for clinics that don’t use a practice-management system. The agent only
                                captures call data; setup, providers, and booking are disabled.
                            </p>
                            <FormMessage />
                        </FormItem>
                    )}
                />
                <Button type="submit" className="w-full">Create Institution</Button>
            </form>
        </Form>
    )
}
