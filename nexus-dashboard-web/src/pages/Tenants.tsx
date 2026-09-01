import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Building2, Plus, RefreshCcw } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/foundation/compat/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/foundation/compat/table";
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
    SheetTrigger,
} from "@/components/foundation/compat/sheet";
import { TenantForm } from "@/components/tenants/TenantForm";
import { InstitutionDetail } from "@/types";
import api from "@/lib/api";
import { toast } from "sonner";
import { Badge } from "@/components/foundation/compat/badge";

export default function Institutions() {
    const navigate = useNavigate();
    const [isOpen, setIsOpen] = useState(false);
    const [institutions, setInstitutions] = useState<InstitutionDetail[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    const fetchInstitutions = async () => {
        setIsLoading(true);
        try {
            const { data } = await api.get<InstitutionDetail[]>("/admin/institutions");
            setInstitutions(data);
        } catch {
            toast.error("Failed to fetch institutions");
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        fetchInstitutions();
    }, []);

    const handleSuccess = () => {
        setIsOpen(false);
        fetchInstitutions();
    };

    return (
        <div className="ui-page ui-page-stack">
            <PageHeader
                icon={Building2}
                title="Institutions"
                actions={
                    <>
                        <Button variant="outline" size="icon" onClick={fetchInstitutions} disabled={isLoading}>
                            <RefreshCcw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                        </Button>
                        <Sheet open={isOpen} onOpenChange={setIsOpen}>
                            <SheetTrigger asChild>
                                <Button>
                                    <Plus className="mr-2 h-4 w-4" /> Add Institution
                                </Button>
                            </SheetTrigger>
                            <SheetContent className="sm:max-w-md">
                                <SheetHeader>
                                    <SheetTitle>Add New Institution</SheetTitle>
                                    <SheetDescription>
                                        Create a new institution. This will trigger an invite email.
                                    </SheetDescription>
                                </SheetHeader>
                                <div className="py-4">
                                    <TenantForm onSuccess={handleSuccess} />
                                </div>
                            </SheetContent>
                        </Sheet>
                    </>
                }
            />
            <div className="overflow-hidden rounded-lg border border-border bg-background/60 shadow-sm">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Name</TableHead>
                            <TableHead>Slug</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead className="text-right">ID</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {institutions.length === 0 && !isLoading && (
                            <TableRow>
                                <TableCell colSpan={4} className="h-24 text-center">
                                    No institutions found.
                                </TableCell>
                            </TableRow>
                        )}
                        {institutions.map((inst) => (
                            <TableRow
                                key={inst.id}
                                className="cursor-pointer"
                                onClick={() => navigate(`/institutions/${inst.slug}`)}
                            >
                                <TableCell className="font-medium">{inst.name}</TableCell>
                                <TableCell>{inst.slug}</TableCell>
                                <TableCell>
                                    <Badge
                                        variant="secondary"
                                        className={inst.is_active
                                            ? "border border-border bg-primary/10 text-primary"
                                            : "border border-border bg-muted text-muted-foreground"}
                                    >
                                        {inst.is_active ? "Active" : "Inactive"}
                                    </Badge>
                                </TableCell>
                                <TableCell className="text-right font-mono text-xs">{inst.id}</TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>
        </div>
    );
}
