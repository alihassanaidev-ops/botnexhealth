import { useCallback, useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import * as z from "zod"
import { Layers, Plus, RefreshCw, Settings2, X, Building2 } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { Button } from "@/components/foundation/compat/button"
import { Card, CardContent } from "@/components/foundation/compat/card"
import { Input } from "@/components/foundation/compat/input"
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/foundation/compat/table"
import {
    Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/foundation/compat/dialog"
import {
    Form, FormControl, FormField, FormItem, FormLabel, FormMessage,
} from "@/components/foundation/compat/form"
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/foundation/compat/select"
import { Skeleton } from "@/components/foundation/compat/skeleton"
import { toast } from "sonner"
import groupsIcon from "@/assets/icons/presentation/groups.png"
import "./groups.css"
import api from "@/lib/api"
import {
    listGroups, createGroup, assignInstitution, unassignInstitution,
    type AdminGroup,
} from "@/lib/group-api"
import type { InstitutionDetail } from "@/types"

const groupSchema = z.object({
    name: z.string().min(2, { message: "Name must be at least 2 characters." }),
    slug: z.string().min(2).regex(/^[a-z0-9-]+$/, { message: "Lowercase alphanumeric with hyphens." }),
    email: z.string().trim().email({ message: "Invalid email address." }),
})

function CreateGroupDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
    const form = useForm<z.infer<typeof groupSchema>>({
        resolver: zodResolver(groupSchema),
        defaultValues: { name: "", slug: "", email: "" },
    })

    async function onSubmit(values: z.infer<typeof groupSchema>) {
        try {
            await createGroup(values)
            toast.success("Group created — invite sent to the group admin")
            form.reset()
            onCreated()
            onClose()
        } catch (err: unknown) {
            const e = err as { response?: { data?: { detail?: string } } }
            toast.error(e?.response?.data?.detail || "Failed to create group")
        }
    }

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="groups-dialog groups-create-dialog">
                <DialogHeader className="groups-dialog-header">
                    <DialogTitle className="groups-dialog-title"><span className="groups-dialog-mark ui-artwork"><img src={groupsIcon} alt="" /></span> New Group</DialogTitle>
                    <DialogDescription>
                        A DSO/practice-group umbrella. The invited user gets the read-only GROUP_ADMIN role over the group's practices.
                    </DialogDescription>
                </DialogHeader>
                <Form {...form}>
                    <form onSubmit={form.handleSubmit(onSubmit)} className="groups-form">
                        <FormField control={form.control} name="name" render={({ field }) => (
                            <FormItem><FormLabel>Group Name</FormLabel><FormControl><Input placeholder="Bright Dental Group" {...field} /></FormControl><FormMessage /></FormItem>
                        )} />
                        <FormField control={form.control} name="slug" render={({ field }) => (
                            <FormItem><FormLabel>Slug</FormLabel><FormControl><Input placeholder="bright-dental-group" {...field} /></FormControl><FormMessage /></FormItem>
                        )} />
                        <FormField control={form.control} name="email" render={({ field }) => (
                            <FormItem><FormLabel>Group Admin Email</FormLabel><FormControl><Input placeholder="ops@brightdental.com" {...field} /></FormControl><FormMessage /></FormItem>
                        )} />
                        <Button type="submit" className="groups-submit">Create Group</Button>
                    </form>
                </Form>
            </DialogContent>
        </Dialog>
    )
}

function ManageMembersDialog({
    group, institutions, onClose, onChanged,
}: { group: AdminGroup | null; institutions: InstitutionDetail[]; onClose: () => void; onChanged: () => void }) {
    const [busy, setBusy] = useState(false)
    const [toAdd, setToAdd] = useState<string>("")

    if (!group) return null
    const members = institutions.filter((i) => i.group_id === group.id)
    const unassigned = institutions.filter((i) => !i.group_id)

    async function doAssign() {
        if (!toAdd || !group) return
        const inst = institutions.find((i) => i.id === toAdd)
        if (!inst) return
        setBusy(true)
        try {
            await assignInstitution(group.slug, inst.slug)
            toast.success(`Added ${inst.name}`)
            setToAdd("")
            onChanged()
        } catch (e) {
            const err = e as { response?: { data?: { detail?: string } } }
            toast.error(err?.response?.data?.detail || "Failed to add")
        } finally {
            setBusy(false)
        }
    }

    async function doRemove(inst: InstitutionDetail) {
        if (!group) return
        setBusy(true)
        try {
            await unassignInstitution(group.slug, inst.slug)
            toast.success(`Removed ${inst.name}`)
            onChanged()
        } catch (e) {
            const err = e as { response?: { data?: { detail?: string } } }
            toast.error(err?.response?.data?.detail || "Failed to remove")
        } finally {
            setBusy(false)
        }
    }

    return (
        <Dialog open={!!group} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="groups-dialog groups-members-dialog">
                <DialogHeader className="groups-dialog-header">
                    <DialogTitle className="groups-dialog-title"><span className="groups-dialog-mark ui-artwork"><img src={groupsIcon} alt="" /></span> {group.name} — members</DialogTitle>
                    <DialogDescription>Assign or remove practices. Members move with a single FK flip — no data is touched.</DialogDescription>
                </DialogHeader>

                <div className="groups-member-picker">
                    <Select value={toAdd} onValueChange={setToAdd}>
                        <SelectTrigger><SelectValue placeholder="Add a practice…" /></SelectTrigger>
                        <SelectContent>
                            {unassigned.length === 0
                                ? <div className="px-2 py-1.5 text-sm text-muted-foreground">No unassigned practices</div>
                                : unassigned.map((i) => <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>)}
                        </SelectContent>
                    </Select>
                    <Button size="sm" onClick={doAssign} disabled={!toAdd || busy} className="groups-member-add"><Plus className="h-3.5 w-3.5" /> Add</Button>
                </div>

                <div className="groups-member-list">
                    {members.length === 0 ? (
                        <p className="groups-member-empty">No practices in this group yet.</p>
                    ) : members.map((m) => (
                        <div key={m.id} className="groups-member-row">
                            <span className="groups-member-name"><span className="groups-member-icon"><Building2 /></span><span>{m.name}</span></span>
                            <Button variant="ghost" size="sm" className="groups-member-remove" disabled={busy} onClick={() => doRemove(m)}>
                                <X className="h-3.5 w-3.5" /> Remove
                            </Button>
                        </div>
                    ))}
                </div>
            </DialogContent>
        </Dialog>
    )
}

export default function Groups() {
    const [groups, setGroups] = useState<AdminGroup[]>([])
    const [institutions, setInstitutions] = useState<InstitutionDetail[]>([])
    const [loading, setLoading] = useState(true)
    const [creating, setCreating] = useState(false)
    const [managing, setManaging] = useState<AdminGroup | null>(null)

    const fetchAll = useCallback(async () => {
        setLoading(true)
        try {
            const [g, instRes] = await Promise.all([
                listGroups(),
                api.get<InstitutionDetail[]>("/admin/institutions"),
            ])
            setGroups(g)
            setInstitutions(instRes.data)
        } catch {
            toast.error("Failed to load groups")
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => { fetchAll() }, [fetchAll])

    // Keep the manage dialog's group reference fresh after a refetch.
    const managingLive = managing ? groups.find((g) => g.id === managing.id) ?? managing : null

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={Layers}
                title="Groups"
                description="DSO / practice-group umbrellas with read-only cross-practice oversight."
                actions={
                    <>
                        <Button variant="outline" size="sm" onClick={fetchAll} disabled={loading} className="gap-1.5">
                            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
                        </Button>
                        <Button size="sm" onClick={() => setCreating(true)} className="gap-1.5"><Plus className="h-3.5 w-3.5" /> New Group</Button>
                    </>
                }
            />

            <Card>
                <CardContent className="p-0">
                    <Table className="w-full text-sm">
                        <TableHeader className="border-b border-border bg-muted">
                            <TableRow>
                                <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Group</TableHead>
                                <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Slug</TableHead>
                                <TableHead className="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Practices</TableHead>
                                <TableHead className="px-4 py-3 text-right text-xs font-semibold text-muted-foreground uppercase tracking-wide">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                Array.from({ length: 3 }).map((_, i) => (
                                    <TableRow key={i}>{Array.from({ length: 4 }).map((__, j) => <TableCell key={j} className="px-4 py-3"><Skeleton className="h-4 w-24" /></TableCell>)}</TableRow>
                                ))
                            ) : groups.length === 0 ? (
                                <TableRow><TableCell colSpan={4} className="h-24 text-center text-muted-foreground">No groups yet. Create one to get started.</TableCell></TableRow>
                            ) : groups.map((g) => (
                                <TableRow key={g.id}>
                                    <TableCell className="px-4 font-medium">{g.name}</TableCell>
                                    <TableCell className="px-4 font-mono text-sm text-muted-foreground">{g.slug}</TableCell>
                                    <TableCell className="px-4 tabular-nums">{g.member_count}</TableCell>
                                    <TableCell className="px-4 text-right">
                                        <Button variant="outline" size="sm" className="gap-1 text-xs" onClick={() => setManaging(g)}>
                                            <Settings2 className="h-3.5 w-3.5" /> Manage members
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <CreateGroupDialog open={creating} onClose={() => setCreating(false)} onCreated={fetchAll} />
            <ManageMembersDialog
                group={managingLive}
                institutions={institutions}
                onClose={() => setManaging(null)}
                onChanged={fetchAll}
            />
        </div>
    )
}
