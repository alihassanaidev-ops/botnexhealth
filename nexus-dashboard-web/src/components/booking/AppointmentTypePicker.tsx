import { useMemo, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import type { AppointmentTypeOption } from "@/lib/booking-link-api"

/**
 * A searchable appointment-type picker for the patient booking page.
 *
 * Built here rather than reaching for a shared combobox because this codebase
 * has none, and adding a dependency for one patient-facing control is not worth
 * it. The demo practice carries five types and a real clinic can carry forty,
 * which is past the point where a plain list stays scannable — so the search
 * box appears once the list is long enough to need it, and not before.
 */
export function AppointmentTypePicker({
    types,
    value,
    onChange,
    disabled,
    allowAny = true,
}: {
    types: AppointmentTypeOption[]
    value: string | null
    onChange: (id: string | null) => void
    disabled?: boolean
    allowAny?: boolean
}) {
    const [open, setOpen] = useState(false)
    const [query, setQuery] = useState("")
    const searchRef = useRef<HTMLInputElement>(null)

    const chosen = types.find((t) => t.id === value)
    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase()
        return q ? types.filter((t) => t.name.toLowerCase().includes(q)) : types
    }, [types, query])

    const showSearch = types.length > 8

    return (
        <Popover
            open={open}
            onOpenChange={(next) => {
                setOpen(next)
                if (next) {
                    setQuery("")
                    // Focus after mount, or the caret lands nowhere and the
                    // on-screen keyboard never opens on a phone.
                    setTimeout(() => searchRef.current?.focus(), 0)
                }
            }}
        >
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="outline"
                    disabled={disabled}
                    className="w-full h-11 justify-between font-normal"
                    aria-label="Appointment type"
                >
                    <span className="truncate">
                        {chosen
                            ? chosen.name
                            : allowAny
                              ? "Any appointment type"
                              : "Choose appointment type"}
                    </span>
                    <span aria-hidden className="ml-2 opacity-50">▾</span>
                </Button>
            </PopoverTrigger>

            <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
                {showSearch && (
                    <div className="p-2 border-b">
                        <Input
                            ref={searchRef}
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            placeholder="Search appointment types"
                            className="h-9"
                        />
                    </div>
                )}

                <div className="max-h-64 overflow-y-auto py-1">
                    {allowAny && (
                        <button
                            type="button"
                            className="w-full text-left px-3 py-2.5 text-sm hover:bg-accent"
                            onClick={() => {
                                onChange(null)
                                setOpen(false)
                            }}
                        >
                            Any appointment type
                        </button>
                    )}

                    {filtered.map((type) => (
                        <button
                            key={type.id}
                            type="button"
                            className="w-full text-left px-3 py-2.5 text-sm hover:bg-accent"
                            onClick={() => {
                                onChange(type.id)
                                setOpen(false)
                            }}
                        >
                            <span className="block">{type.name}</span>
                            {type.duration_minutes ? (
                                <span className="block text-xs text-muted-foreground">
                                    {type.duration_minutes} minutes
                                </span>
                            ) : null}
                        </button>
                    ))}

                    {filtered.length === 0 && (
                        <p className="px-3 py-3 text-sm text-muted-foreground">
                            Nothing matches that search.
                        </p>
                    )}
                </div>
            </PopoverContent>
        </Popover>
    )
}
