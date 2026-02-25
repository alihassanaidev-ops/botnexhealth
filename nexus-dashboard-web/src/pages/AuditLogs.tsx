import { useState, useEffect } from "react";
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
import { toast } from "sonner";

import { listAuditLogs, AuditLog } from "@/lib/tenant-api";

export default function AuditLogs() {
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const pageSize = 50;

    async function fetchLogs(currentPage: number = page) {
        setLoading(true);
        try {
            const data = await listAuditLogs(currentPage, pageSize);
            setLogs(data.items);
            setTotal(data.total);
            setPage(currentPage);
        } catch (err: any) {
            console.error("Failed to fetch audit logs:", err);
            toast.error(err.message || "Unknown error occurred.");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        fetchLogs(1);
    }, []);

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
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Audit Logs</h1>
                    <p className="text-muted-foreground mt-2">
                        View compliance and system activity across your account.
                    </p>
                </div>
                <Button variant="outline" onClick={() => fetchLogs(page)} disabled={loading}>
                    <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                    Refresh Logs
                </Button>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Activity Log</CardTitle>
                    <CardDescription>A chronological record of actions performed in your system.</CardDescription>
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
                            <div className="border rounded-md">
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
                            <div className="flex items-center justify-between px-2">
                                <div className="text-sm text-muted-foreground">
                                    Showing {Math.min((page - 1) * pageSize + 1, total)} to {Math.min(page * pageSize, total)} of {total} entries
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
