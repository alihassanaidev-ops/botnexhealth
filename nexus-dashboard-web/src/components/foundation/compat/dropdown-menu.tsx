import * as React from "react"
import * as Menu from "@radix-ui/react-dropdown-menu"
import { Check, Circle } from "lucide-react"

import { cn } from "@/lib/utils"
import "../compat.css"

export const DropdownMenu = Menu.Root
export const DropdownMenuTrigger = Menu.Trigger
export const DropdownMenuGroup = Menu.Group
export const DropdownMenuPortal = Menu.Portal
export const DropdownMenuSub = Menu.Sub
export const DropdownMenuRadioGroup = Menu.RadioGroup
export const DropdownMenuContent = React.forwardRef<React.ElementRef<typeof Menu.Content>, React.ComponentPropsWithoutRef<typeof Menu.Content>>(function DropdownMenuContent({ className, sideOffset = 6, ...props }, ref) { return <Menu.Portal><Menu.Content ref={ref} sideOffset={sideOffset} className={cn("compat-menu-content", className)} {...props} /></Menu.Portal> })
export const DropdownMenuItem = React.forwardRef<React.ElementRef<typeof Menu.Item>, React.ComponentPropsWithoutRef<typeof Menu.Item> & { inset?: boolean }>(function DropdownMenuItem({ className, inset, ...props }, ref) { return <Menu.Item ref={ref} className={cn("compat-menu-item", inset && "pl-8", className)} {...props} /> })
export const DropdownMenuCheckboxItem = React.forwardRef<React.ElementRef<typeof Menu.CheckboxItem>, React.ComponentPropsWithoutRef<typeof Menu.CheckboxItem>>(function DropdownMenuCheckboxItem({ className, children, checked, ...props }, ref) { return <Menu.CheckboxItem ref={ref} checked={checked} className={cn("compat-menu-item", className)} {...props}><Menu.ItemIndicator><Check /></Menu.ItemIndicator>{children}</Menu.CheckboxItem> })
export const DropdownMenuRadioItem = React.forwardRef<React.ElementRef<typeof Menu.RadioItem>, React.ComponentPropsWithoutRef<typeof Menu.RadioItem>>(function DropdownMenuRadioItem({ className, children, ...props }, ref) { return <Menu.RadioItem ref={ref} className={cn("compat-menu-item", className)} {...props}><Menu.ItemIndicator><Circle /></Menu.ItemIndicator>{children}</Menu.RadioItem> })
export const DropdownMenuLabel = React.forwardRef<React.ElementRef<typeof Menu.Label>, React.ComponentPropsWithoutRef<typeof Menu.Label> & { inset?: boolean }>(function DropdownMenuLabel({ className, inset, ...props }, ref) { return <Menu.Label ref={ref} className={cn("compat-menu-label", inset && "pl-8", className)} {...props} /> })
export const DropdownMenuSeparator = React.forwardRef<React.ElementRef<typeof Menu.Separator>, React.ComponentPropsWithoutRef<typeof Menu.Separator>>(function DropdownMenuSeparator({ className, ...props }, ref) { return <Menu.Separator ref={ref} className={cn("compat-menu-separator", className)} {...props} /> })
export function DropdownMenuShortcut({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) { return <span className={cn("compat-menu-shortcut", className)} {...props} /> }
export const DropdownMenuSubTrigger = Menu.SubTrigger
export const DropdownMenuSubContent = Menu.SubContent
