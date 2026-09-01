import { forwardRef, type ComponentProps } from "react"

import { cn } from "@/lib/utils"
import "../data-table.css"

export const Table = forwardRef<HTMLTableElement, ComponentProps<"table">>(function Table({ className, ...props }, ref) {
    return <div className="ui-table-scroll"><table ref={ref} className={cn("ui-table", className)} {...props} /></div>
})
export const TableHeader = forwardRef<HTMLTableSectionElement, ComponentProps<"thead">>(function TableHeader({ className, ...props }, ref) {
    return <thead ref={ref} className={cn("ui-table-header", className)} {...props} />
})
export const TableBody = forwardRef<HTMLTableSectionElement, ComponentProps<"tbody">>(function TableBody({ className, ...props }, ref) {
    return <tbody ref={ref} className={cn("ui-table-body", className)} {...props} />
})
export const TableFooter = forwardRef<HTMLTableSectionElement, ComponentProps<"tfoot">>(function TableFooter({ className, ...props }, ref) {
    return <tfoot ref={ref} className={cn("ui-table-footer", className)} {...props} />
})
export const TableRow = forwardRef<HTMLTableRowElement, ComponentProps<"tr">>(function TableRow({ className, ...props }, ref) {
    return <tr ref={ref} className={cn("ui-table-row", className)} {...props} />
})
export const TableHead = forwardRef<HTMLTableCellElement, ComponentProps<"th">>(function TableHead({ className, ...props }, ref) {
    return <th ref={ref} className={cn("ui-table-head", className)} {...props} />
})
export const TableCell = forwardRef<HTMLTableCellElement, ComponentProps<"td">>(function TableCell({ className, ...props }, ref) {
    return <td ref={ref} className={cn("ui-table-cell", className)} {...props} />
})
export const TableCaption = forwardRef<HTMLTableCaptionElement, ComponentProps<"caption">>(function TableCaption({ className, ...props }, ref) {
    return <caption ref={ref} className={cn("ui-table-caption", className)} {...props} />
})
