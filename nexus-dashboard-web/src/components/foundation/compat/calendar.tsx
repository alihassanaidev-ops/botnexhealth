import * as React from "react"
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react"
import { DayPicker } from "react-day-picker"

import { cn } from "@/lib/utils"
import "../compat.css"

export function Calendar({ className, showOutsideDays = true, components, ...props }: React.ComponentProps<typeof DayPicker>) {
    return <DayPicker showOutsideDays={showOutsideDays} className={cn("compat-calendar", className)} components={{ Chevron: ({ orientation, ...iconProps }) => orientation === "left" ? <ChevronLeft {...iconProps} /> : orientation === "right" ? <ChevronRight {...iconProps} /> : <ChevronDown {...iconProps} />, ...components }} {...props} />
}
