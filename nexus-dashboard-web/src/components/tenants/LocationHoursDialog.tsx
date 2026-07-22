import { useState, useEffect } from "react";
import { Loader2, Plus, Clock, Trash2 } from "lucide-react";
import { toast } from "sonner";
import api from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { FormSkeleton } from "@/components/ui/skeletons";
import type { Location, OperatingHoursResponse, OperatingHoursEntry, BreakResponse, BreakCreateRequest } from "@/types";

interface LocationHoursDialogProps {
    institutionSlug: string;
    location: Location | null;
    onClose: () => void;
}

const DAYS = [
    { value: 0, label: "Monday" },
    { value: 1, label: "Tuesday" },
    { value: 2, label: "Wednesday" },
    { value: 3, label: "Thursday" },
    { value: 4, label: "Friday" },
    { value: 5, label: "Saturday" },
    { value: 6, label: "Sunday" },
];

export function LocationHoursDialog({ institutionSlug, location, onClose }: LocationHoursDialogProps) {
    const [isLoadingHours, setIsLoadingHours] = useState(false);
    const [isSavingHours, setIsSavingHours] = useState(false);
    const [hours, setHours] = useState<OperatingHoursEntry[]>(() =>
        DAYS.map(day => ({
            day_of_week: day.value,
            is_open: day.value >= 0 && day.value <= 4, // default Mon-Fri open
            open_time: day.value >= 0 && day.value <= 4 ? "09:00" : null,
            close_time: day.value >= 0 && day.value <= 4 ? "17:00" : null,
        }))
    );

    const [isLoadingBreaks, setIsLoadingBreaks] = useState(false);
    const [breaks, setBreaks] = useState<BreakResponse[]>([]);

    // New Break Form State
    const [isAddingBreak, setIsAddingBreak] = useState(false);
    const [newBreakName, setNewBreakName] = useState("Lunch");
    const [newBreakDay, setNewBreakDay] = useState<string>("all");
    const [newBreakStart, setNewBreakStart] = useState("12:00");
    const [newBreakEnd, setNewBreakEnd] = useState("13:00");

    useEffect(() => {
        if (!location) return;

        async function fetchHoursAndBreaks() {
            setIsLoadingHours(true);
            setIsLoadingBreaks(true);
            try {
                const [hoursRes, breaksRes] = await Promise.all([
                    api.get<OperatingHoursResponse[]>(`/admin/institutions/${institutionSlug}/locations/${location!.slug}/operating-hours`),
                    api.get<BreakResponse[]>(`/admin/institutions/${institutionSlug}/locations/${location!.slug}/breaks`)
                ]);

                if (hoursRes.data && hoursRes.data.length > 0) {
                    const loadedHours = DAYS.map(day => {
                        const existing = hoursRes.data.find(h => h.day_of_week === day.value);
                        if (existing) {
                            return {
                                day_of_week: existing.day_of_week,
                                is_open: existing.is_open,
                                open_time: existing.open_time || "09:00",
                                close_time: existing.close_time || "17:00"
                            };
                        }
                        return {
                            day_of_week: day.value,
                            is_open: false,
                            open_time: "09:00",
                            close_time: "17:00"
                        };
                    });
                    setHours(loadedHours);
                }
                setBreaks(breaksRes.data);
            } catch {
                toast.error("Failed to load operating hours.");
            } finally {
                setIsLoadingHours(false);
                setIsLoadingBreaks(false);
            }
        }

        fetchHoursAndBreaks();
    }, [location, institutionSlug]);

    const handleSaveHours = async () => {
        if (!location) return;
        setIsSavingHours(true);
        try {
            await api.put(`/admin/institutions/${institutionSlug}/locations/${location.slug}/operating-hours`, {
                hours: hours.map(h => ({
                    ...h,
                    open_time: h.is_open ? h.open_time : null,
                    close_time: h.is_open ? h.close_time : null
                }))
            });
            toast.success("Operating hours saved successfully.");
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to save operating hours.");
        } finally {
            setIsSavingHours(false);
        }
    };

    const handleUpdateHour = (dayValue: number, field: keyof OperatingHoursEntry, value: boolean | string | null) => {
        setHours(prev => prev.map(h => h.day_of_week === dayValue ? { ...h, [field]: value } : h));
    };

    const handleAddBreak = async () => {
        if (!location || !newBreakName || !newBreakStart || !newBreakEnd) return;
        setIsAddingBreak(true);
        try {
            const payload: BreakCreateRequest = {
                name: newBreakName,
                day_of_week: newBreakDay === "all" ? null : parseInt(newBreakDay),
                start_time: newBreakStart,
                end_time: newBreakEnd
            };
            const { data } = await api.post<BreakResponse>(
                `/admin/institutions/${institutionSlug}/locations/${location.slug}/breaks`,
                payload
            );
            setBreaks(prev => [...prev, data]);
            toast.success("Break added successfully.");
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to add break.");
        } finally {
            setIsAddingBreak(false);
        }
    };

    const handleDeleteBreak = async (breakId: string) => {
        if (!location) return;
        try {
            await api.delete(`/admin/institutions/${institutionSlug}/locations/${location.slug}/breaks/${breakId}`);
            setBreaks(prev => prev.filter(b => b.id !== breakId));
            toast.success("Break removed.");
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            toast.error(error?.response?.data?.detail || "Failed to remove break.");
        }
    };

    const getDayLabel = (dayVal: number | null) => {
        if (dayVal === null) return "Every Day";
        return DAYS.find(d => d.value === dayVal)?.label || "Unknown";
    };

    if (!location) return null;

    return (
        <Dialog open={!!location} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto border-border bg-card">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Clock className="w-5 h-5 text-muted-foreground" />
                        Operating Hours - {location.name}
                    </DialogTitle>
                    <DialogDescription>
                        Set the standard weekly operating hours and scheduled breaks for this location.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-8 py-4">
                    {/* Operating Hours Section */}
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-lg font-medium">Weekly Hours</h3>
                        </div>
                        {isLoadingHours ? (
                            <FormSkeleton rows={4} />
                        ) : (
                            <div className="space-y-3 rounded-lg border border-border bg-card p-4">
                                {hours.map((hour) => (
                                    <div key={hour.day_of_week} className="flex items-center gap-4 rounded-md border border-border bg-muted px-3 py-2">
                                        <div className="w-32 flex items-center gap-2">
                                            <Switch
                                                checked={hour.is_open}
                                                onCheckedChange={(c) => handleUpdateHour(hour.day_of_week, "is_open", c)}
                                            />
                                            <span className="text-sm font-medium text-foreground">{DAYS.find(d => d.value === hour.day_of_week)?.label}</span>
                                        </div>
                                        <div className="flex-1 flex items-center gap-2">
                                            <Input
                                                type="time"
                                                className="w-32"
                                                disabled={!hour.is_open}
                                                value={hour.open_time || "09:00"}
                                                onChange={(e) => handleUpdateHour(hour.day_of_week, "open_time", e.target.value)}
                                            />
                                            <span className="text-muted-foreground text-sm">to</span>
                                            <Input
                                                type="time"
                                                className="w-32"
                                                disabled={!hour.is_open}
                                                value={hour.close_time || "17:00"}
                                                onChange={(e) => handleUpdateHour(hour.day_of_week, "close_time", e.target.value)}
                                            />
                                            {!hour.is_open && (
                                                <span className="text-sm text-muted-foreground pl-4">Closed</span>
                                            )}
                                        </div>
                                    </div>
                                ))}
                                <div className="pt-4 flex justify-end">
                                    <Button onClick={handleSaveHours} disabled={isSavingHours}>
                                        {isSavingHours && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                        Save Hours
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Scheduled Breaks Section */}
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-lg font-medium">Scheduled Breaks</h3>
                        </div>
                        <div className="space-y-4 rounded-lg border border-border bg-card p-4">
                            {isLoadingBreaks ? (
                                <FormSkeleton rows={4} />
                            ) : breaks.length === 0 ? (
                                <p className="text-sm text-muted-foreground py-2">No breaks configured.</p>
                            ) : (
                                <div className="space-y-2">
                                    {breaks.map((brk) => (
                                        <div key={brk.id} className="flex items-center justify-between rounded-md border border-border bg-muted p-3">
                                            <div className="grid grid-cols-4 w-full items-center gap-4">
                                                <span className="font-medium text-sm col-span-1 truncate text-foreground">{brk.name}</span>
                                                <span className="text-sm text-foreground col-span-1">{getDayLabel(brk.day_of_week)}</span>
                                                <span className="text-sm col-span-1">{brk.start_time} - {brk.end_time}</span>
                                                <div className="col-span-1 flex justify-end">
                                                    <Button variant="ghost" size="icon" onClick={() => handleDeleteBreak(brk.id)}>
                                                        <Trash2 className="h-4 w-4 text-destructive" />
                                                    </Button>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}

                            <div className="mt-4 border-t border-border pt-4">
                                <h4 className="text-sm font-medium mb-3">Add New Break</h4>
                                <div className="grid grid-cols-5 gap-3 items-end">
                                    <div className="col-span-1 space-y-1">
                                        <Label className="text-xs">Name</Label>
                                        <Input value={newBreakName} onChange={(e) => setNewBreakName(e.target.value)} placeholder="Lunch" />
                                    </div>
                                    <div className="col-span-1 space-y-1">
                                        <Label className="text-xs">Day</Label>
                                        <Select value={newBreakDay} onValueChange={setNewBreakDay}>
                                            <SelectTrigger>
                                                <SelectValue placeholder="Every Day" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="all">Every Day</SelectItem>
                                                {DAYS.map(d => (
                                                    <SelectItem key={d.value} value={d.value.toString()}>{d.label}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="col-span-1 space-y-1">
                                        <Label className="text-xs">Start</Label>
                                        <Input type="time" value={newBreakStart} onChange={(e) => setNewBreakStart(e.target.value)} />
                                    </div>
                                    <div className="col-span-1 space-y-1">
                                        <Label className="text-xs">End</Label>
                                        <Input type="time" value={newBreakEnd} onChange={(e) => setNewBreakEnd(e.target.value)} />
                                    </div>
                                    <div className="col-span-1">
                                        <Button className="w-full" onClick={handleAddBreak} disabled={isAddingBreak || !newBreakName || !newBreakStart || !newBreakEnd}>
                                            {isAddingBreak ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4 mr-2" />}
                                            Add
                                        </Button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
