/**
 * Which institution an admin page is acting on.
 *
 * The per-institution email surfaces are reached by two different callers. A
 * clinic admin is pinned to their own institution and never sees a choice. A
 * platform admin has no institution of their own, so they must name one — the
 * API refuses the request otherwise, deliberately, rather than guessing a
 * tenant.
 *
 * Pages consume this instead of branching on the role themselves: render
 * `picker`, wait for `ready`, and thread `institutionId` into the API call.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react"

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/context/AuthContext"
import { listInstitutionsDetailed } from "@/lib/admin-api"
import type { InstitutionDetail } from "@/types"

interface InstitutionScope {
    /** Undefined for a clinic admin — the API pins them to their own. */
    institutionId: string | undefined
    /** False while a platform admin still has no institution chosen. */
    ready: boolean
    /** Null for anyone who has no choice to make. */
    picker: ReactNode
}

export function useInstitutionScope(): InstitutionScope {
    const { user } = useAuth()
    const isPlatformAdmin = user?.role === "SUPER_ADMIN"

    const [institutions, setInstitutions] = useState<InstitutionDetail[]>([])
    const [selected, setSelected] = useState<string | undefined>(undefined)
    // Starts true because the fetch below runs on the first render for the one
    // role that has a choice to make; every other role never reads it.
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        if (!isPlatformAdmin) return
        let cancelled = false
        listInstitutionsDetailed()
            .then((rows) => {
                if (cancelled) return
                const active = rows.filter((row) => row.is_active)
                setInstitutions(active)
                // One tenant is not a choice; skip straight past the picker.
                if (active.length === 1) setSelected(active[0].id)
            })
            .catch(() => {
                if (!cancelled) setInstitutions([])
            })
            .finally(() => {
                if (!cancelled) setLoading(false)
            })
        return () => {
            cancelled = true
        }
    }, [isPlatformAdmin])

    const picker = useMemo(() => {
        if (!isPlatformAdmin) return null
        return (
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-muted-foreground">Practice</span>
                <Select
                    value={selected}
                    onValueChange={setSelected}
                    disabled={loading || institutions.length === 0}
                >
                    <SelectTrigger className="w-64" aria-label="Practice">
                        <SelectValue
                            placeholder={loading ? "Loading…" : "Choose a practice"}
                        />
                    </SelectTrigger>
                    <SelectContent>
                        {institutions.map((institution) => (
                            <SelectItem key={institution.id} value={institution.id}>
                                {institution.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
        )
    }, [isPlatformAdmin, institutions, selected, loading])

    return {
        institutionId: isPlatformAdmin ? selected : undefined,
        ready: !isPlatformAdmin || Boolean(selected),
        picker,
    }
}
