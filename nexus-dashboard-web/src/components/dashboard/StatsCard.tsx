import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
        card: "border-border bg-gradient-to-br from-primary to-primary2 text-primary-foreground shadow-lg shadow-primary/20",
        title: "text-primary-foreground/85",
        iconWrap: "bg-primary-foreground/15 text-primary-foreground",
        value: "text-primary-foreground",
        description: "text-primary-foreground/85",
    },
    primarySoft: {
        card: "border-border bg-gradient-to-br from-secondary via-accent to-primary2/25 text-foreground shadow-md shadow-primary/10",
        title: "text-muted-foreground",
        iconWrap: "bg-primary/15 text-primary",
        value: "text-foreground",
        description: "text-muted-foreground",
    },
    accent: {
        card: "border-accent-foreground/20 bg-gradient-to-br from-accent to-secondary text-foreground shadow-md shadow-accent-foreground/10",
        title: "text-muted-foreground",
        iconWrap: "bg-accent-foreground/15 text-accent-foreground",
        value: "text-foreground",
        description: "text-muted-foreground",
    },
} as const

export function StatsCard({ title, value, description, icon: Icon, tone = "neutral" }: StatsCardProps) {
    const styles = TONE_STYLES[tone]

    return (
        <Card className={cn("transition-all duration-200 hover:-translate-y-0.5", styles.card)}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className={cn("text-sm font-medium", styles.title)}>{title}</CardTitle>
                <div className={cn("rounded-lg p-2", styles.iconWrap)}>
                    <Icon className="h-4 w-4" />
                </div>
            </CardHeader>
            <CardContent>
                <div className={cn("text-2xl font-bold", styles.value)}>{value}</div>
                <p className={cn("text-xs", styles.description)}>{description}</p>
            </CardContent>
        </Card>
    )
}
