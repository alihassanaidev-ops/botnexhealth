import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, RefreshCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
    SheetTrigger,
} from "@/components/ui/sheet";
import { TenantForm } from "@/components/tenants/TenantForm";
import { Tenant } from "@/types";
import api from "@/lib/api";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";

export default function Tenants() {
    const navigate = useNavigate();
    const [isOpen, setIsOpen] = useState(false);
    const [tenants, setTenants] = useState<Tenant[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    const fetchTenants = async () => {
        setIsLoading(true);
        try {
            const { data } = await api.get<Tenant[]>("/admin/tenants");
            setTenants(data);
        } catch (error) {
            console.error("Failed to fetch tenants", error);
            toast.error("Failed to fetch tenants");
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        fetchTenants();
    }, []);

    const handleSuccess = () => {
        setIsOpen(false);
        fetchTenants();
    };

    return (
        <div className="flex-1 space-y-4 p-8 pt-6">
            <div className="flex items-center justify-between space-y-2">
                <h2 className="text-3xl font-bold tracking-tight">Tenants</h2>
                <div className="flex items-center space-x-2">
                    <Button variant="outline" size="icon" onClick={fetchTenants} disabled={isLoading}>
                        <RefreshCcw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                    </Button>
                    <Sheet open={isOpen} onOpenChange={setIsOpen}>
                        <SheetTrigger asChild>
                            <Button>
                                <Plus className="mr-2 h-4 w-4" /> Add Tenant
                            </Button>
                        </SheetTrigger>
                        <SheetContent>
                            <SheetHeader>
                                <SheetTitle>Add New Tenant</SheetTitle>
                                <SheetDescription>
                                    Create a new tenant. This will trigger an invite email.
                                </SheetDescription>
                            </SheetHeader>
                            <div className="py-4">
                                <TenantForm onSuccess={handleSuccess} />
                            </div>
                        </SheetContent>
                    </Sheet>
                </div>
            </div>
            <div className="border rounded-md">
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
                        {tenants.length === 0 && !isLoading && (
                            <TableRow>
                                <TableCell colSpan={4} className="h-24 text-center">
                                    No tenants found.
                                </TableCell>
                            </TableRow>
                        )}
                        {tenants.map((tenant) => (
                            <TableRow
                                key={tenant.id}
                                className="cursor-pointer"
                                onClick={() => navigate(`/tenants/${tenant.slug}`)}
                            >
                                <TableCell className="font-medium">{tenant.name}</TableCell>
                                <TableCell>{tenant.slug}</TableCell>
                                <TableCell>
                                    <Badge variant={tenant.is_active ? "default" : "secondary"}>
                                        {tenant.is_active ? "Active" : "Inactive"}
                                    </Badge>
                                </TableCell>
                                <TableCell className="text-right font-mono text-xs">{tenant.id}</TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>
        </div>
    );
}
