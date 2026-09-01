import { useTheme } from "next-themes"
import { Toaster as Sonner } from "sonner"

type ToasterProps = React.ComponentProps<typeof Sonner>
export function Toaster(props: ToasterProps) {
    const { theme = "system" } = useTheme()
    return <Sonner theme={theme as ToasterProps["theme"]} toastOptions={{ classNames: { toast: "border-border bg-popover text-popover-foreground shadow-xl", description: "text-muted-foreground", actionButton: "bg-primary text-primary-foreground", cancelButton: "bg-muted text-muted-foreground" } }} {...props} />
}
