/**
 * Dropdown for institution admins to switch the active location.
 *
 * Hidden entirely for users that can't switch (LOCATION_ADMIN, STAFF —
 * the backend pins them to user.location_id) and for institutions with
 * a single active location (no switching to do).
 */

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { useLocationContext } from "@/context/LocationContext"

export function LocationSelector() {
    const { locations, selectedLocationId, setSelectedLocationId, canSwitch, isLoading } =
        useLocationContext()

    if (!canSwitch) return null
    if (isLoading) return null
    if (locations.length === 0) return null
    if (locations.length === 1) {
        return (
            <div
                aria-label="Active location"
                className="flex h-8 w-full items-center truncate rounded-md border border-sidebar-border bg-sidebar-accent/30 px-3 text-xs font-medium text-sidebar-foreground"
                data-testid="location-selector"
                title={locations[0].name}
            >
                {locations[0].name}
            </div>
        )
    }

    return (
        <Select
            value={selectedLocationId ?? undefined}
            onValueChange={setSelectedLocationId}
        >
            <SelectTrigger
                aria-label="Active location"
                className="h-8 w-full text-xs"
                data-testid="location-selector"
            >
                <SelectValue placeholder="Select location" />
            </SelectTrigger>
            <SelectContent>
                {locations.map((loc) => (
                    <SelectItem key={loc.id} value={loc.id}>
                        {loc.name}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    )
}
