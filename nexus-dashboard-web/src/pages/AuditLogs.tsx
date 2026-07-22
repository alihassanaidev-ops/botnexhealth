import { useState, useEffect, useCallback } from "react";
import { format } from "date-fns";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RefreshCw, ChevronLeft, ChevronRight, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { TableSkeleton } from "@/components/ui/skeletons";
import { toast } from "sonner";

import { listAuditLogs, AuditLog } from "@/lib/tenant-api";
import { useAuth } from "@/context/AuthContext";

export default function AuditLogs() {
    const { user } = useAuth();
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const pageSize = 50;

    const fetchLogs = useCallback(async (currentPage: number) => {
        setLoading(true);
        try {
            const scope = user?.role === "LOCATION_ADMIN"
                ? "location"
                : "institution";
            const data = await listAuditLogs(currentPage, pageSize, scope);
            setLogs(data.items);
            setTotal(data.total);
            setPage(currentPage);
        } catch (err: unknown) {
            const error = err as { message?: string };
            toast.error(error?.message || "Failed to fetch audit logs.");
        } finally {
            setLoading(false);
        }
    }, [user?.role, pageSize]);

    useEffect(() => {
        fetchLogs(1);
    }, [fetchLogs]);

    function renderOutcomeBadge(outcome: string) {
        if (outcome === "SUCCESS") {
            return <Badge variant="default" className="bg-green-600 hover:bg-green-700">{outcome}</Badge>;
        }
        if (outcome.startsWith("FAILURE")) {
            return <Badge variant="destructive">{outcome}</Badge>;
        }
        return <Badge variant="secondary">{outcome}</Badge>;
    }

    function renderActorBadge(actor: string) {
        switch (actor) {
            case "RETELL_AGENT":
                return <Badge variant="outline" className="text-blue-600 border-blue-200 bg-blue-50">Automation</Badge>;
            case "ADMIN":
                return <Badge variant="outline" className="text-purple-600 border-purple-200 bg-purple-50">Super Admin</Badge>;
            case "SYSTEM":
                return <Badge variant="outline" className="text-gray-600 border-gray-200 bg-gray-50">System</Badge>;
            case "API_CLIENT":
                return <Badge variant="outline" className="text-orange-600 border-orange-200 bg-orange-50">API Client</Badge>;
            default:
                return <Badge variant="secondary">{actor}</Badge>;
        }
    }

    return (
        <div className="relative space-y-6 bg-background">
            <div className="fixed inset-0 overflow-hidden pointer-events-none"><div className="absolute -top-32 -right-32 w-[420px] h-[420px] bg-transparent dark:bg-violet-700/20 rounded-full blur-[100px]" /></div>
            <PageHeader
                icon={ShieldCheck}
                title="Audit Logs"
                description="View compliance and system activity for your allowed scope."
                actions={
                    <Button variant="outline" onClick={() => fetchLogs(page)} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                        Refresh Logs
                    </Button>
                }
            />

            <Card>
                <CardHeader>
                    <CardTitle>Activity Log</CardTitle>
                    <CardDescription>A chronological record of actions performed in your system.</CardDescription>
                </CardHeader>
                <CardContent>
                    {loading && logs.length === 0 ? (
                        <TableSkeleton rows={8} cols={4} />
                    ) : logs.length === 0 ? (
                        <div className="text-center p-8 text-muted-foreground">
                            No audit logs found.
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Timestamp</TableHead>
                                            <TableHead>Actor</TableHead>
                                            <TableHead>Action</TableHead>
                                            <TableHead>Target Resource</TableHead>
                                            <TableHead>Outcome</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {logs.map((log) => (
                                            <TableRow key={log.id}>
                                                <TableCell className="whitespace-nowrap">
                                                    {format(new Date(log.timestamp), "MMM d, yyyy h:mm a")}
                                                </TableCell>
                                                <TableCell>{renderActorBadge(log.actor)}</TableCell>
                                                <TableCell className="font-mono text-xs">{log.action}</TableCell>
                                                <TableCell className="text-sm text-muted-foreground max-w-xs truncate" title={log.target_resource}>
                                                    {log.target_resource}
                                                </TableCell>
                                                <TableCell>{renderOutcomeBadge(log.outcome)}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>

                            {/* Pagination Controls */}
                            <div className="flex flex-col gap-3 border-t border-border px-2 pt-4 sm:flex-row sm:items-center sm:justify-between">
                                <div className="text-sm text-muted-foreground">
                                    {total > 0
                                        ? `Showing ${(page - 1) * pageSize + 1} to ${Math.min(page * pageSize, total)} of ${total} entries`
                                        : "No entries found"}
                                </div>
                                <div className="flex items-center space-x-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => fetchLogs(page - 1)}
                                        disabled={page === 1 || loading}
                                    >
                                        <ChevronLeft className="h-4 w-4 mr-1" />
                                        Previous
                                    </Button>
                                    <div className="text-sm font-medium mx-2">
                                        Page {page} of {Math.max(1, Math.ceil(total / pageSize))}
                                    </div>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => fetchLogs(page + 1)}
                                        disabled={page >= Math.ceil(total / pageSize) || loading}
                                    >
                                        Next
                                        <ChevronRight className="h-4 w-4 ml-1" />
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
