import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { useAuth } from "@/context/AuthContext"
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { toast } from "sonner"
import { Link, useSearchParams } from "react-router-dom"

const formSchema = z.object({
    password: z.string()
        .min(8, { message: "Password must be at least 8 characters" })
        .regex(/[a-z]/, { message: "Password must include a lowercase letter" })
        .regex(/[A-Z]/, { message: "Password must include an uppercase letter" })
        .regex(/[0-9]/, { message: "Password must include a number" }),
    confirmPassword: z.string()
}).refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ["confirmPassword"],
})

export default function SetPassword() {
    const { updatePassword } = useAuth()
    const [loading, setLoading] = useState(false)
    const [searchParams] = useSearchParams()

    const token = searchParams.get("token")?.trim() || ""
    const flowParam = searchParams.get("flow")
    const flow = flowParam === "invite" || flowParam === "reset" ? flowParam : null
    const isResetFlow = flow === "reset"

    const form = useForm<z.infer<typeof formSchema>>({
        resolver: zodResolver(formSchema),
        defaultValues: {
            password: "",
            confirmPassword: "",
        },
    })

    async function onSubmit(values: z.infer<typeof formSchema>) {
        if (!token || !flow) {
            toast.error("This password link is invalid or incomplete")
            return
        }

        setLoading(true)
        try {
            await updatePassword(values.password, token, flow)
        } catch (err: unknown) {
            const error = err as { message?: string };
            toast.error(error?.message || "Failed to update password")
        } finally {
            setLoading(false)
        }
    }

    if (!token || !flow) {
        return (
            <div className="relative flex h-screen w-full items-center justify-center bg-background p-4">
                <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
                <Card className="w-full max-w-sm border-border bg-gradient-to-b from-card to-accent/20 shadow-lg">
                    <CardHeader>
                        <CardTitle className="text-2xl">Invalid Link</CardTitle>
                        <CardDescription>
                            This invite or password reset link is missing required information.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Button asChild className="w-full">
                            <Link to="/login">Go To Login</Link>
                        </Button>
                    </CardContent>
                </Card>
            </div>
        )
    }

    return (
        <div className="relative flex h-screen w-full items-center justify-center bg-background p-4">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <Card className="w-full max-w-sm border-border bg-gradient-to-b from-card to-accent/20 shadow-lg">
                <CardHeader>
                    <CardTitle className="text-2xl">
                        {isResetFlow ? "Reset Password" : "Set Password"}
                    </CardTitle>
                    <CardDescription>
                        {isResetFlow
                            ? "Choose a new password for your account."
                            : "Create a password to activate your account."}
                        Must be at least 8 characters with uppercase, lowercase, and a number.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Form {...form}>
                        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
                            <FormField
                                control={form.control}
                                name="password"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>New Password</FormLabel>
                                        <FormControl>
                                            <Input type="password" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="confirmPassword"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Confirm Password</FormLabel>
                                        <FormControl>
                                            <Input type="password" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <Button type="submit" className="w-full" disabled={loading}>
                                {loading
                                    ? "Updating..."
                                    : isResetFlow
                                        ? "Reset Password"
                                        : "Set Password"}
                            </Button>
                        </form>
                    </Form>
                </CardContent>
            </Card>
        </div>
    )
}
