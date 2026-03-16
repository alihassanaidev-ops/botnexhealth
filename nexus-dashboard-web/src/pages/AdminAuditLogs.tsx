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
import { Loader2, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

import { listAdminAuditLogs } from "@/lib/admin-api";
import { AuditLog } from "@/lib/tenant-api";

export default function AdminAuditLogs() {
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const [institutionIdFilter, setInstitutionIdFilter] = useState("");
    const pageSize = 50;

    const fetchLogs = useCallback(async (currentPage: number, institutionId: string) => {
        setLoading(true);
        try {
            const data = await listAdminAuditLogs(currentPage, pageSize, institutionId || undefined);
            setLogs(data.items);
            setTotal(data.total);
            setPage(currentPage);
        } catch (err: unknown) {
            console.error("Failed to fetch admin audit logs:", err);
            const error = err as { message?: string };
            toast.error(error?.message || "Unknown error occurred.");
        } finally {
            setLoading(false);
        }
    }, [pageSize]);

    useEffect(() => {
        // Debounce fetching if filtering by institution ID
        const timer = setTimeout(() => {
            fetchLogs(1, institutionIdFilter);
        }, 300);
        return () => clearTimeout(timer);
    }, [institutionIdFilter, fetchLogs]);

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
                return <Badge variant="outline" className="text-blue-600 border-blue-200 bg-blue-50">Retell AI</Badge>;
            case "ADMIN":
                return <Badge variant="outline" className="text-purple-600 border-purple-200 bg-purple-50">Admin</Badge>;
            case "SYSTEM":
                return <Badge variant="outline" className="text-gray-600 border-gray-200 bg-gray-50">System</Badge>;
            case "API_CLIENT":
                return <Badge variant="outline" className="text-orange-600 border-orange-200 bg-orange-50">API Client</Badge>;
            default:
                return <Badge variant="secondary">{actor}</Badge>;
        }
    }

    return (
        <div className="space-y-6 bg-gradient-to-b from-background via-background to-accent/20">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Platform Audit Logs</h1>
                    <p className="text-muted-foreground mt-2">
                        View compliance and system activity across all institutions.
                    </p>
                </div>
                <div className="flex items-center space-x-4">
                    <div className="relative">
                        <Input
                            type="text"
                            placeholder="Filter by Institution ID..."
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 min-w-[300px]"
                            value={institutionIdFilter}
                            onChange={(e) => setInstitutionIdFilter(e.target.value)}
                        />
                    </div>
                    <Button variant="outline" onClick={() => fetchLogs(page, institutionIdFilter)} disabled={loading}>
                        <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                        Refresh Logs
                    </Button>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Global Activity Log</CardTitle>
                    <CardDescription>A chronological record of actions performed across all institutions.</CardDescription>
                </CardHeader>
                <CardContent>
                    {loading && logs.length === 0 ? (
                        <div className="flex justify-center p-8">
                            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                        </div>
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
                                            <TableHead>Institution ID</TableHead>
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
                                                <TableCell className="font-mono text-xs text-muted-foreground">
                                                    {log.institution_id}
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
                            <div className="flex items-center justify-between px-2">
                                <div className="text-sm text-muted-foreground">
                                    Showing {Math.min((page - 1) * pageSize + 1, total)} to {Math.min(page * pageSize, total)} of {total} entries
                                </div>
                                <div className="flex items-center space-x-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => fetchLogs(page - 1, institutionIdFilter)}
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
                                        onClick={() => fetchLogs(page + 1, institutionIdFilter)}
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
