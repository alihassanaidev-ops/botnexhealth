import { Users, DollarSign, CreditCard, Activity } from "lucide-react"
import { StatsCard } from "@/components/dashboard/StatsCard"
import { OverviewChart } from "@/components/dashboard/OverviewChart"

export default function Dashboard() {
    return (
        <div className="relative flex-1 space-y-4 bg-background p-8 pt-6">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <div className="flex items-center justify-between space-y-2">
                <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
            </div>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <StatsCard
                    title="Total Revenue"
                    value="$45,231.89"
                    description="+20.1% from last month"
                    icon={DollarSign}
                />
                <StatsCard
                    title="Subscriptions"
                    value="+2350"
                    description="+180.1% from last month"
                    icon={Users}
                />
                <StatsCard
                    title="Sales"
                    value="+12,234"
                    description="+19% from last month"
                    icon={CreditCard}
                />
                <StatsCard
                    title="Active Now"
                    value="+573"
                    description="+201 since last hour"
                    icon={Activity}
                />
            </div>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
                <OverviewChart />
                {/* We could add RecentSales widget here if needed, taking up 3 cols */}
            </div>
        </div>
    )
}
