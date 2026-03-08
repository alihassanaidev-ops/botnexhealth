import { useEffect, useState } from "react"
import { Loader2, MailPlus, Users } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    inviteStaff,
    listInstitutionPortalLocations,
    type InstitutionPortalLocation,
} from "@/lib/institution-portal-api"

export default function LocationAdminPanel() {
    const [loading, setLoading] = useState(true)
    const [inviting, setInviting] = useState(false)
    const [location, setLocation] = useState<InstitutionPortalLocation | null>(null)
    const [email, setEmail] = useState("")

    useEffect(() => {
        async function loadLocation() {
            setLoading(true)
            try {
                const locations = await listInstitutionPortalLocations()
                setLocation(locations[0] ?? null)
            } catch (error: any) {
                toast.error(error?.response?.data?.detail || "Failed to load location")
            } finally {
                setLoading(false)
            }
        }

        void loadLocation()
    }, [])

    async function handleInvite() {
        if (!location || !email.trim()) return
        setInviting(true)
        try {
            await inviteStaff(location.slug, email.trim())
            toast.success(`Staff invite sent to ${email.trim()}`)
            setEmail("")
        } catch (error: any) {
            toast.error(error?.response?.data?.detail || "Failed to send invite")
        } finally {
            setInviting(false)
        }
    }

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Location Admin</h1>
                <p className="mt-1 text-muted-foreground">
                    Invite staff for your assigned location.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Assigned Location</CardTitle>
                    <CardDescription>
                        {loading ? "Loading location..." : location ? `${location.name} (${location.slug})` : "No location assigned"}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Fetching location details...
                        </div>
                    ) : null}
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Users className="h-4 w-4" />
                        Invite Staff
                    </CardTitle>
                    <CardDescription>
                        Staff users have location-scoped access and cannot invite other users.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="staff-email">Email</Label>
                        <Input
                            id="staff-email"
                            type="email"
                            placeholder="staff@clinic.com"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                    void handleInvite()
                                }
                            }}
                            disabled={!location || inviting}
                        />
                    </div>
                    <Button onClick={handleInvite} disabled={!location || inviting || !email.trim()}>
                        {inviting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <MailPlus className="mr-2 h-4 w-4" />}
                        Send Invite
                    </Button>
                </CardContent>
            </Card>
        </div>
    )
}
