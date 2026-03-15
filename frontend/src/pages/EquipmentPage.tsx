import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"
import { useDemoMode } from "@/lib/demoMode"
import ConfirmationModal from "@/components/ui/ConfirmationModal"
import { ErrorState } from "@/components/ui/ErrorState"
import { EmptyState } from "@/components/ui/EmptyState"
import { CassetteTape, HardDrive, Server } from "lucide-react"
import LibrarySelector from "@/components/LibrarySelector"
import { ContextPanel } from "@/components/ContextPanel"
import CalibrationWizard from "@/components/CalibrationWizard"

const MOCK_EQUIPMENT = {
    drives: [
        { id: 'DRV-01', type: 'LTO-8 SAS', status: 'Online', activity: 'Writing', speed: '285 MB/s', tape: 'LTO-8-0042', health: 98 },
        { id: 'DRV-02', type: 'LTO-8 SAS', status: 'Online', activity: 'Idle', speed: '0 MB/s', tape: 'None', health: 100 },
        { id: 'DRV-03', type: 'LTO-7 SAS', status: 'Offline', activity: 'Disabled', speed: '0 MB/s', tape: 'None', health: 85 },
    ],
    libraries: [
        { id: 'LIB-01', name: 'Quantum Scalable', status: 'Ready', slots: '24 / 40', drives: 2 },
    ]
};

export default function EquipmentPage() {
    const { isDemoMode } = useDemoMode();
    const [drives, setDrives] = useState<any[]>(isDemoMode ? MOCK_EQUIPMENT.drives : []);
    const [libraries, setLibraries] = useState<any[]>(isDemoMode ? MOCK_EQUIPMENT.libraries : []);
    const [loading, setLoading] = useState(!isDemoMode);
    const [error, setError] = useState<string | null>(null);
    const [scanning, setScanning] = useState(false);

    const [confirmModal, setConfirmModal] = useState<{
        isOpen: boolean;
        driveId: string | null;
        tape: string | null;
        type: 'unmount' | 'lock';
    }>({
        isOpen: false,
        driveId: null,
        tape: null,
        type: 'unmount'
    });

    const [selectedTape, setSelectedTape] = useState<any | null>(null);
    const [panelOpen, setPanelOpen] = useState(false);
    const [calibrationOpen, setCalibrationOpen] = useState(false);

    useEffect(() => {
        if (isDemoMode) {
            setDrives(MOCK_EQUIPMENT.drives);
            setLibraries(MOCK_EQUIPMENT.libraries);
            setLoading(false);
            return;
        }

        const fetchData = async () => {
            setLoading(true);
            const driveRes = await api.getDriveHealth();
            const statsRes = await api.getSystemStats();

            if (driveRes.success && driveRes.data) {
                setDrives(driveRes.data.drives.map(d => ({
                    id: d.id,
                    type: d.type || 'LTO-8 SAS',
                    status: d.status === 'idle' ? 'Online' : d.status === 'busy' ? 'Online' : d.status === 'error' ? 'Error' : 'Offline',
                    activity: d.status === 'busy' ? 'Working' : 'Idle',
                    speed: d.status === 'busy' ? '--- MB/s' : '0 MB/s',
                    tape: d.current_tape || 'None',
                    health: d.cleaning_needed ? 90 : 100
                })));
                setError(null);
            } else {
                setError(driveRes.error || 'Failed to sync with hardware controller');
            }

            if (statsRes.success && statsRes.data) {
                setLibraries([{
                    id: 'LIB-DEFAULT',
                    name: 'Integrated Library',
                    status: 'Active',
                    slots: `${statsRes.data.tapes_online} / ${statsRes.data.total_slots}`,
                    drives: driveRes.data?.drives.length || 0
                }]);
            }
            setLoading(false);
        };

        fetchData();
        const interval = setInterval(fetchData, 30000);
        return () => clearInterval(interval);
    }, [isDemoMode]);

    const handleRescan = async () => {
        setScanning(true);
        // Simulate or call real rescan if exists
        await api.scanLibrary('fast');
        setScanning(false);
    };

    const handleLockTape = async (barcode: string) => {
        // Default to 10 year retention for simplified appliance feel
        const defaultExpiry = new Date();
        defaultExpiry.setFullYear(defaultExpiry.getFullYear() + 10);

        const res = await api.lockTape(barcode, defaultExpiry.toISOString());
        if (res.success) {
            // Refresh detailed view if open
            if (selectedTape && selectedTape.barcode === barcode) {
                const updated = await api.getTapeDetails(barcode);
                if (updated.success && updated.data) setSelectedTape(updated.data.tape);
            }
        } else {
            alert('Failed to lock tape: ' + res.error);
        }
    };

    const handleUnmount = async (driveId: string) => {
        // Find drive index
        const idx = drives.findIndex(d => d.id === driveId);
        if (idx === -1) return;

        const res = await api.unloadTape(idx);
        if (res.success) {
            // Update local state for immediate feedback
            setDrives(prev => prev.map(d => d.id === driveId ? { ...d, tape: 'None', activity: 'Idle', status: 'Online' } : d));
        } else {
            console.error('Failed to unmount: ' + (res.error || 'Unknown error'));
        }
    };

    const handleInspectHealth = async (barcode: string) => {
        if (!barcode || barcode === 'None') return;
        setLoading(true);
        const res = await api.getTapeDetails(barcode);
        if (res.success && res.data) {
            setSelectedTape(res.data.tape);
            setPanelOpen(true);
        }
        setLoading(false);
    };
    return (
        <div className="flex flex-col gap-8">
            <header className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-bold tracking-tight text-white uppercase antialiased">Equipment Topology</h1>
                    <div className="flex items-center gap-2 mt-1">
                        {scanning ? (
                            <>
                                <div className="size-1 rounded-full bg-primary animate-pulse"></div>
                                <span className="text-[10px] text-primary font-mono uppercase tracking-widest">Scanning Hardware...</span>
                            </>
                        ) : (
                            <>
                                <div className="size-1 rounded-full bg-green-500"></div>
                                <span className="text-[10px] text-[#71717a] font-mono uppercase tracking-widest">Topology Synchronized</span>
                            </>
                        )}
                    </div>
                </div>
                <div className="flex items-center gap-6">
                    <LibrarySelector />
                    <div className="flex gap-3">
                        <button
                            onClick={handleRescan}
                            disabled={scanning}
                            className="flex items-center gap-2 bg-[#121214] border border-[#27272a] hover:border-[#3f3f46] text-[#71717a] hover:text-white px-4 py-2 rounded text-[10px] font-bold transition-all uppercase tracking-widest disabled:opacity-50"
                        >
                            <span className={cn("material-symbols-outlined text-[18px]", scanning && "animate-spin")}>refresh</span>
                            {scanning ? 'Scanning...' : 'Re-Scan Bus'}
                        </button>
                        <button
                            onClick={handleRescan}
                            disabled={scanning}
                            className="flex items-center gap-2 bg-primary hover:bg-green-400 text-black px-4 py-2 rounded text-[10px] font-bold transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)] uppercase tracking-widest disabled:opacity-50"
                        >
                            <span className="material-symbols-outlined text-[18px]">add</span>
                            Connect Device
                        </button>
                        <button
                            onClick={() => setCalibrationOpen(true)}
                            className="flex items-center gap-2 bg-[#18181b] border border-[#27272a] hover:border-primary/50 text-[#a1a1aa] hover:text-white px-4 py-2 rounded text-[10px] font-bold transition-all uppercase tracking-widest"
                        >
                            <span className="material-symbols-outlined text-[18px]">settings_suggest</span>
                            Calibrate Drive Map
                        </button>
                    </div>
                </div>
            </header>

            {error ? (
                <ErrorState
                    title="Hardware Query Failed"
                    message={error}
                    retry={() => window.location.reload()}
                    retryLabel="Retry Inspection"
                />
            ) : (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    {/* Drives Section */}
                    <div className="space-y-6">
                        <div className="flex items-center justify-between px-2">
                            <h2 className="text-[10px] font-bold text-white uppercase tracking-[0.3em]">Physical_Drives</h2>
                            <span className="text-[10px] font-mono text-[#71717a]">{drives.length} Detected</span>
                        </div>
                        <div className="space-y-4">
                            {loading ? (
                                <div className="animate-pulse space-y-4">
                                    <div className="h-40 bg-[#18181b] rounded border border-[#27272a]"></div>
                                    <div className="h-40 bg-[#18181b] rounded border border-[#27272a]"></div>
                                </div>
                            ) : drives.length === 0 ? (
                                <EmptyState
                                    title="No Tape Drives Detected"
                                    className="py-12"
                                />
                            ) : drives.map((drive) => (
                                <div key={drive.id} className="bg-[#121214] border border-[#27272a] rounded p-6 group hover:border-primary/30 transition-all relative overflow-hidden">
                                    <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-br from-transparent to-white/[0.02] pointer-events-none"></div>
                                    <div className="flex items-start justify-between relative z-10">
                                        <div className="flex gap-4">
                                            <div className={cn(
                                                "size-12 rounded flex items-center justify-center border",
                                                drive.status === 'Online' ? "bg-primary/10 border-primary/20 text-primary" : "bg-[#18181b] border-[#27272a] text-[#71717a]"
                                            )}>
                                                <HardDrive className="size-7" />
                                            </div>
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <h3 className="text-sm font-bold text-white uppercase tracking-tight font-mono">{drive.id}</h3>
                                                    <span className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest bg-black/40 px-1.5 py-0.5 rounded border border-[#27272a]">{drive.type}</span>
                                                </div>
                                                <div className="flex items-center gap-3 mt-2">
                                                    <span className={cn(
                                                        "text-[9px] font-bold uppercase tracking-widest flex items-center gap-1.5",
                                                        drive.status === 'Online' ? "text-primary" : "text-[#71717a]"
                                                    )}>
                                                        <div className={cn("size-1.5 rounded-full", drive.status === 'Online' ? "bg-primary animate-pulse" : "bg-destructive")}></div>
                                                        {drive.status}
                                                    </span>
                                                    <div className="size-1 rounded-full bg-[#27272a]"></div>
                                                    <button
                                                        onClick={() => drive.tape !== 'None' && handleInspectHealth(drive.tape)}
                                                        className={cn(
                                                            "text-[9px] font-bold uppercase tracking-widest hover:underline",
                                                            drive.tape !== 'None' ? "text-primary cursor-pointer" : "text-[#71717a] cursor-default"
                                                        )}
                                                    >
                                                        Health: {drive.health}%
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                        <div className="text-right">
                                            <div className="text-xs font-mono text-white/50 uppercase tracking-tighter">{drive.activity}</div>
                                            <div className="text-lg font-bold font-mono text-white mt-1">{drive.speed}</div>
                                        </div>
                                    </div>

                                    {drive.tape !== 'None' && (
                                        <div className="mt-6 pt-6 border-t border-[#27272a] flex items-center justify-between">
                                            <div className="flex items-center gap-3">
                                                <span className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Mounted_Media:</span>
                                                <span className="text-[10px] font-mono font-bold text-primary flex items-center gap-2 bg-primary/5 px-2 py-1 rounded border border-primary/10">
                                                    <CassetteTape className="size-3.5" />
                                                    {drive.tape}
                                                </span>
                                            </div>
                                            <button
                                                onClick={() => setConfirmModal({ isOpen: true, driveId: drive.id, tape: drive.tape, type: 'unmount' })}
                                                className="group/unmount flex items-center gap-2 px-3 py-1.5 rounded bg-warning/10 border border-warning/30 text-warning hover:bg-warning/20 hover:border-warning/60 transition-all text-[9px] font-bold uppercase tracking-widest cursor-pointer"
                                            >
                                                <span className="material-symbols-outlined text-[16px] group-hover/unmount:animate-bounce">eject</span>
                                                Unmount
                                            </button>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Libraries Section */}
                    <div className="space-y-6">
                        <div className="flex items-center justify-between px-2">
                            <h2 className="text-[10px] font-bold text-white uppercase tracking-[0.3em]">Media_Changers</h2>
                            <span className="text-[10px] font-mono text-[#71717a]">{libraries.length} Active</span>
                        </div>
                        <div className="space-y-4">
                            {loading ? (
                                <div className="animate-pulse space-y-4">
                                    <div className="h-40 bg-[#18181b] rounded border border-[#27272a]"></div>
                                </div>
                            ) : libraries.length === 0 ? (
                                <EmptyState
                                    title="No Libraries Detected"
                                    className="py-12"
                                />
                            ) : libraries.map((lib) => (
                                <div key={lib.id} className="bg-[#121214] border border-[#27272a] rounded p-8 group hover:border-primary/30 transition-all">
                                    <div className="flex items-start justify-between mb-8">
                                        <div className="flex gap-5">
                                            <div className="size-14 rounded bg-primary/10 border border-primary/20 text-primary flex items-center justify-center shadow-[0_0_15px_rgba(25,230,100,0.1)]">
                                                <Server className="size-8" />
                                            </div>
                                            <div>
                                                <h3 className="text-lg font-bold text-white uppercase tracking-tight">{lib.name}</h3>
                                                <div className="flex items-center gap-3 mt-1.5">
                                                    <span className="text-[10px] font-bold text-primary uppercase tracking-widest">{lib.status}</span>
                                                    <div className="size-1 rounded-full bg-[#27272a]"></div>
                                                    <span className="text-[10px] font-mono text-[#71717a]">{lib.id}</span>
                                                </div>
                                            </div>
                                        </div>
                                        <div className="text-right">
                                            <div className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] mb-1">Status</div>
                                            <div className="text-xs font-mono text-primary flex items-center justify-end gap-1.5">
                                                <div className="size-1 rounded-full bg-primary ring-4 ring-primary/10"></div>
                                                ONLINE
                                            </div>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-2 gap-6 bg-black/30 rounded p-6 border border-[#27272a]">
                                        <div>
                                            <div className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] mb-2">Slot_Capacity</div>
                                            <div className="flex items-end gap-2">
                                                <span className="text-2xl font-bold font-mono text-white leading-none">{lib.slots.split(' / ')[0]}</span>
                                                <span className="text-xs text-[#71717a] font-mono pb-0.5">/ {lib.slots.split(' / ')[1]}</span>
                                            </div>
                                            <div className="w-full h-1 bg-[#27272a] rounded-full mt-3 overflow-hidden">
                                                <div className="h-full bg-primary w-[60%] rounded-full shadow-[0_0_8px_rgba(25,230,100,0.5)]"></div>
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] mb-2">Assigned_Drives</div>
                                            <div className="text-2xl font-bold font-mono text-white leading-none">{lib.drives}</div>
                                            <div className="flex gap-1 mt-3">
                                                {[1, 2, 3, 4].map(i => (
                                                    <div key={i} className={cn("h-1 flex-1 rounded-full", i <= lib.drives ? "bg-primary" : "bg-[#27272a]")}></div>
                                                ))}
                                            </div>
                                        </div>
                                    </div>

                                    <div className="mt-8 flex gap-3">
                                        <button className="flex-1 py-3 rounded bg-[#18181b] border border-[#27272a] text-[10px] font-bold text-[#71717a] hover:text-white uppercase tracking-widest transition-all">Inventory Browse</button>
                                        <button className="flex-1 py-3 rounded bg-[#18181b] border border-[#27272a] text-[10px] font-bold text-[#71717a] hover:text-white uppercase tracking-widest transition-all">Move Command</button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            <ConfirmationModal
                isOpen={confirmModal.isOpen}
                onClose={() => setConfirmModal({ isOpen: false, driveId: null, tape: null, type: 'unmount' })}
                onConfirm={() => {
                    if (confirmModal.type === 'unmount') {
                        confirmModal.driveId && handleUnmount(confirmModal.driveId);
                    } else if (confirmModal.type === 'lock') {
                        confirmModal.tape && handleLockTape(confirmModal.tape);
                    }
                }}
                title={confirmModal.type === 'unmount' ? "Unmount Media" : "Apply Permanent WORM Lock"}
                message={confirmModal.type === 'unmount'
                    ? `Are you sure you want to unmount tape ${confirmModal.tape} from ${confirmModal.driveId}? The media will be returned to its storage slot.`
                    : `CRITICAL: You are about to apply a Permanent WORM Lock to tape ${confirmModal.tape}. Once locked, this media CANNOT be formatted, wiped, or overwritten for the duration of its retention period. This action is irreversible.`
                }
                variant={confirmModal.type === 'unmount' ? "info" : "danger"}
                confirmText={confirmModal.type === 'unmount' ? "Unmount" : "Lock Media Permanently"}
            />

            <ContextPanel
                tape={selectedTape}
                isOpen={panelOpen}
                onClose={() => setPanelOpen(false)}
                onUnload={() => {
                    if (selectedTape?.drive_id !== undefined) {
                        handleUnmount(`DRV-${String(selectedTape.drive_id).padStart(2, '0')}`);
                    }
                }}
                onLock={(barcode) => setConfirmModal({ isOpen: true, driveId: null, tape: barcode, type: 'lock' })}
            />

            <CalibrationWizard
                isOpen={calibrationOpen}
                onClose={() => setCalibrationOpen(false)}
            />
        </div>
    )
}

