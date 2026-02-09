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
import { toast } from "sonner"
import api from "@/lib/api"

const tenantSchema = z.object({
    name: z.string().min(2, { message: "Name must be at least 2 characters." }),
    slug: z.string().min(2, { message: "Slug is required." }).regex(/^[a-z0-9-]+$/, { message: "Slug must be lowercase alphanumeric with hyphens." }),
    email: z.string().email({ message: "Invalid email address." }),
})

interface TenantFormProps {
    onSuccess: () => void
}

export function TenantForm({ onSuccess }: TenantFormProps) {
    const form = useForm<z.infer<typeof tenantSchema>>({
        resolver: zodResolver(tenantSchema),
        defaultValues: {
            name: "",
            slug: "",
            email: "",
        },
    })

    async function onSubmit(values: z.infer<typeof tenantSchema>) {
        try {
            await api.post("/admin/tenants", values)
            toast.success("Tenant created successfully")
            form.reset()
            onSuccess()
        } catch (error: any) {
            console.error("Failed to create tenant", error)
            toast.error(error.response?.data?.detail || "Failed to create tenant")
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
                            <FormLabel>Tenant Name</FormLabel>
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
                <Button type="submit" className="w-full">Create Tenant</Button>
            </form>
        </Form>
    )
}
