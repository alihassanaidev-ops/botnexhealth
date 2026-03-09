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

const formSchema = z.object({
    email: z.string().email({ message: "Invalid email address" }),
    password: z.string().min(6, { message: "Password must be at least 6 characters" }),
})

export default function Login() {
    const { signInWithSupabase, requestPasswordReset } = useAuth()
    const [loading, setLoading] = useState(false)
    const [resetLoading, setResetLoading] = useState(false)

    const form = useForm<z.infer<typeof formSchema>>({
        resolver: zodResolver(formSchema),
        defaultValues: {
            email: "",
            password: "",
        },
    })

    async function onSubmit(values: z.infer<typeof formSchema>) {
        setLoading(true)
        try {
            await signInWithSupabase(values.email, values.password);
            // onAuthStateChange in AuthContext handles navigation after token exchange
        } catch (error) {
            // Error handled in AuthContext with toast
        } finally {
            setLoading(false)
        }
    }

    async function onForgotPassword() {
        const email = form.getValues("email").trim();
        const valid = await form.trigger("email");
        if (!valid || !email) return;

        setResetLoading(true);
        try {
            await requestPasswordReset(email);
            // Generic success message avoids account enumeration.
            form.setValue("password", "");
            toast.success("If an account exists, a password reset email has been sent.");
        } catch (error: any) {
            // Keep surface minimal - Supabase message is usually actionable.
            form.setError("email", { message: error?.message || "Failed to send reset email" });
        } finally {
            setResetLoading(false);
        }
    }

    return (
        <div className="flex h-screen w-full items-center justify-center bg-gray-50">
            <Card className="w-full max-w-sm">
                <CardHeader>
                    <CardTitle className="text-2xl">Login</CardTitle>
                    <CardDescription>
                        Enter your email below to login to your account.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Form {...form}>
                        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
                            <FormField
                                control={form.control}
                                name="email"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Email</FormLabel>
                                        <FormControl>
                                            <Input placeholder="m@example.com" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <FormField
                                control={form.control}
                                name="password"
                                render={({ field }) => (
                                    <FormItem>
                                        <FormLabel>Password</FormLabel>
                                        <FormControl>
                                            <Input type="password" {...field} />
                                        </FormControl>
                                        <FormMessage />
                                    </FormItem>
                                )}
                            />
                            <Button type="submit" className="w-full" disabled={loading}>
                                {loading ? "Signing in..." : "Sign in"}
                            </Button>
                            <Button
                                type="button"
                                variant="ghost"
                                className="w-full"
                                disabled={resetLoading}
                                onClick={onForgotPassword}
                            >
                                {resetLoading ? "Sending reset link..." : "Forgot password?"}
                            </Button>
                        </form>
                    </Form>
                </CardContent>
            </Card>
        </div>
    )
}
