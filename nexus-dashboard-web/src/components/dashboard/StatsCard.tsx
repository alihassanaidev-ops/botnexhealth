import { LucideIcon } from "lucide-react"
import { cn } from "@/lib/utils"

interface StatsCardProps {
    title: string
    value: string
    description: string
    icon: LucideIcon
    tone?: "neutral" | "primary" | "primarySoft" | "accent"
}

const TONE_STYLES = {
    neutral: {
        card: "border-border/80 bg-card shadow-sm",
        title: "text-muted-foreground",
        iconWrap: "bg-muted text-foreground/80",
        value: "text-foreground",
        description: "text-muted-foreground",
    },
    primary: {
        card: "border-primary/25 bg-card shadow-sm",
        title: "text-muted-foreground",
        iconWrap: "bg-primary/12 text-primary",
        value: "text-foreground",
        description: "text-muted-foreground",
    },
    primarySoft: {
        card: "border-border/80 bg-card shadow-sm",
        title: "text-muted-foreground",
        iconWrap: "bg-primary/15 text-primary",
        value: "text-foreground",
        description: "text-muted-foreground",
    },
    accent: {
        card: "border-accent-foreground/20 bg-card shadow-sm",
        title: "text-muted-foreground",
        iconWrap: "bg-accent-foreground/15 text-accent-foreground",
        value: "text-foreground",
        description: "text-muted-foreground",
    },
} as const

export function StatsCard({ title, value, description, icon: Icon, tone = "neutral" }: StatsCardProps) {
    const styles = TONE_STYLES[tone]

    return (
        <section className={cn("rounded-xl border p-5 transition-colors duration-150 hover:border-foreground/15", styles.card)}>
            <div className="flex flex-row items-center justify-between pb-3">
                <h3 className={cn("text-sm font-medium", styles.title)}>{title}</h3>
                <div className={cn("rounded-lg p-2", styles.iconWrap)}>
                    <Icon className="h-4 w-4" />
                </div>
            </div>
            <div>
                <div className={cn("text-2xl font-bold", styles.value)}>{value}</div>
                <p className={cn("text-xs", styles.description)}>{description}</p>
            </div>
        </section>
    )
}
