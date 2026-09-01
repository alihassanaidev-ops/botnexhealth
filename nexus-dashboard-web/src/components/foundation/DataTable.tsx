import { forwardRef, type ComponentProps } from "react"

import "./data-table.css"

export const UiTable = forwardRef<HTMLTableElement, ComponentProps<"table">>(function UiTable(
    { className = "", ...props },
    ref,
) {
    return (
        <div className="ui-table-scroll">
            <table ref={ref} className={`ui-table ${className}`.trim()} {...props} />
        </div>
    )
})

export const UiTableHeader = forwardRef<HTMLTableSectionElement, ComponentProps<"thead">>(function UiTableHeader(
    { className = "", ...props },
    ref,
) {
    return <thead ref={ref} className={`ui-table-header ${className}`.trim()} {...props} />
})

export const UiTableBody = forwardRef<HTMLTableSectionElement, ComponentProps<"tbody">>(function UiTableBody(
    { className = "", ...props },
    ref,
) {
    return <tbody ref={ref} className={`ui-table-body ${className}`.trim()} {...props} />
})

export const UiTableRow = forwardRef<HTMLTableRowElement, ComponentProps<"tr">>(function UiTableRow(
    { className = "", ...props },
    ref,
) {
    return <tr ref={ref} className={`ui-table-row ${className}`.trim()} {...props} />
})

export const UiTableHead = forwardRef<HTMLTableCellElement, ComponentProps<"th">>(function UiTableHead(
    { className = "", ...props },
    ref,
) {
    return <th ref={ref} className={`ui-table-head ${className}`.trim()} {...props} />
})

export const UiTableCell = forwardRef<HTMLTableCellElement, ComponentProps<"td">>(function UiTableCell(
    { className = "", ...props },
    ref,
) {
    return <td ref={ref} className={`ui-table-cell ${className}`.trim()} {...props} />
})
