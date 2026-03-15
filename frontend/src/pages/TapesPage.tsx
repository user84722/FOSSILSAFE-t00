import { useState, useEffect } from "react"
import { useDemoMode } from "@/lib/demoMode"
import { api } from "@/lib/api"
import { ErrorState } from "@/components/ui/ErrorState"
import { EmptyState } from "@/components/ui/EmptyState"
import { ContextPanel } from "@/components/ContextPanel"
import RestoreWizard from "@/components/RestoreWizard"
import ConfirmationModal from "@/components/ui/ConfirmationModal"
import PromptModal from "@/components/ui/PromptModal"
import { useToast } from "@/contexts/ToastContext"
import {
    CassetteTape,
    Search,
    RefreshCw,
    ScanBarcode,
    QrCode,
    Inbox,
    ChevronLeft,
    ChevronRight,
    ChevronsLeft,
    ChevronsRight,
    ShieldCheck,
    ShieldAlert,
    Shield,
    HelpCircle
} from "lucide-react"

const MOCK_TAPES = [
    { barcode: 'A00124L8', alias: 'Archive_2023_Q1', location: 'Slot 004', status: 'Online', capacity: 93, used: '11.2TB', total: '12.0TB', type: 'LTO-8', date: '2023-10-24', mount_count: 42, error_count: 0, trust: 'trusted' },
    { barcode: 'A00125L8', alias: 'Backup_Daily_01', location: 'Drive 1', status: 'Busy', capacity: 51, used: '6.1TB', total: '12.0TB', type: 'LTO-8', date: '2023-10-25', mount_count: 12, error_count: 0, trust: 'trusted' },
    { barcode: 'B00451L7', alias: 'Legal_Hold_2022', location: 'Slot 012', status: 'Offline', capacity: 100, used: '6.0TB', total: '6.0TB', type: 'LTO-7', date: '2022-11-15', mount_count: 8, error_count: 0, trust: 'verified' },
    { barcode: 'C99102L6', alias: 'Legacy_Project_X', location: 'Vault', status: 'Error', capacity: 88, used: '2.2TB', total: '2.5TB', type: 'LTO-6', date: '2020-05-10', mount_count: 156, error_count: 3, trust: 'untrusted' },
    { barcode: 'A00126L8', alias: 'Empty_Spare_01', location: 'Slot 002', status: 'Online', capacity: 0, used: '0TB', total: '12.0TB', type: 'LTO-8', date: '--', mount_count: 0, error_count: 0, trust: 'unknown' },
    { barcode: 'CLN001CU', alias: 'Universal_Cleaner', location: 'Slot 024', status: 'Online', capacity: 0, used: '--', total: '--', type: 'Cleaning', date: '--', mount_count: 5, error_count: 0, trust: 'trusted', is_cleaning: true, cleaning_lives: 45 },
];



export default function TapesPage() {
    const { isDemoMode } = useDemoMode();
    const { showToast } = useToast();
    const [tapes, setTapes] = useState<any[]>(isDemoMode ? MOCK_TAPES : []);
    const [selectedBarcode, setSelectedBarcode] = useState<string | null>(isDemoMode ? 'A00124L8' : null);
    const [loading, setLoading] = useState(!isDemoMode);
    const [error, setError] = useState<string | null>(null);
    const [scanning, setScanning] = useState(false);
    const [showRestoreWizard, setShowRestoreWizard] = useState(false);

    // Pagination state for large libraries
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [searchQuery, setSearchQuery] = useState('');
    const [stats, setStats] = useState<any>(null);

    const [manualBarcode, setManualBarcode] = useState('');
    const [manualLoading, setManualLoading] = useState(false);
    const [confirmModal, setConfirmModal] = useState({ isOpen: false, title: '', message: '', onConfirm: () => { }, variant: 'info' as 'info' | 'danger' | 'warning' });
    const [promptModal, setPromptModal] = useState({ isOpen: false, title: '', message: '', placeholder: '', onConfirm: (_: string) => { }, variant: 'info' as 'info' | 'danger' });

    const selectedTape = tapes.find(t => t.barcode === selectedBarcode);

    // Filter and paginate tapes
    const formatCapacity = (bytes: number) => {
        if (!bytes) return '0.0TB';
        const tb = bytes / (1000 ** 4);
        return `${tb.toFixed(1)}TB`;
    };

    const filteredTapes = tapes.filter(t => {
        if (!searchQuery) return true;
        // Search matches barcode or alias
        const matchBarcode = (t.barcode?.toLowerCase() || "").includes(searchQuery.toLowerCase());
        const matchAlias = (t.alias?.toLowerCase() || "").includes(searchQuery.toLowerCase());
        return matchBarcode || matchAlias;
    });
    const totalPages = Math.ceil(filteredTapes.length / pageSize);
    const paginatedTapes = filteredTapes.slice((currentPage - 1) * pageSize, currentPage * pageSize);
    const totalCapacity = tapes.reduce((sum, t) => sum + parseFloat(t.total?.replace('TB', '') || '0'), 0);

    const handleScanLibrary = async () => {
        if (isDemoMode) return;

        setScanning(true);
        try {
            const res = await api.scanLibrary('fast');
            if (res.success && res.data) {
                const mapped = res.data.tapes.map(t => ({
                    ...t,
                    barcode: t.barcode,
                    alias: t.alias || t.barcode,
                    location: t.location_type === 'drive' ? `Drive ${t.drive_id ?? 0}` : `Slot ${String(t.slot_number || 0).padStart(3, '0')}`,
                    location_type: t.location_type,
                    drive_id: t.drive_id,
                    status: t.location_type === 'drive' ? `Drive ${t.drive_id ?? 0}` : t.status === 'available' ? 'Online' : t.status === 'in_use' ? 'Busy' : t.status === 'error' ? 'Error' : 'Offline',
                    capacity: (t.capacity_bytes ?? 0) > 0 ? Math.round((t.used_bytes || 0) / (t.capacity_bytes ?? 1) * 100) : 0,
                    used: (t.used_bytes ?? 0) > 0 ? formatCapacity(t.used_bytes!) : '0.0TB',
                    total: (t.capacity_bytes ?? 0) > 0 ? formatCapacity(t.capacity_bytes!) : '0.0TB',
                    type: 'LTO-8',
                    date: '--',
                    mount_count: t.mount_count || 0,
                    error_count: t.error_count || 0,
                    trust: t.trust_status || 'unknown',
                    is_cleaning: t.is_cleaning_tape || t.barcode?.startsWith('CLN'),
                    is_cleaning_tape: t.is_cleaning_tape || t.barcode?.startsWith('CLN'),
                    cleaning_lives: t.cleaning_remaining_uses
                }));
                setTapes(mapped);
                if (mapped.length > 0 && !selectedBarcode) setSelectedBarcode(mapped[0].barcode);
                showToast('Library scan complete', 'success');
            } else {
                showToast('Library scan failed', 'error');
            }
        } catch (e) {
            showToast('Failed to scan library', 'error');
        } finally {
            setScanning(false);
        }
    };

    const handleManualLoad = async () => {
        if (!manualBarcode) return;
        setManualLoading(true);
        try {
            const res = await api.loadTape(manualBarcode, 0);
            if (res.success) {
                await handleScanLibrary(); // Refresh view
                setManualBarcode('');
                showToast('Tape loaded successfully', 'success');
            } else {
                showToast(res.error || 'Failed to load tape', 'error');
            }
        } catch (err) {
            console.error('Manual load failed:', err);
            showToast('Failed to load tape', 'error');
        } finally {
            setManualLoading(false);
        }
    };

    const handleInitializeTape = () => {
        if (!selectedBarcode) return;
        setPromptModal({
            isOpen: true,
            title: 'Initialize Tape',
            message: `This will format tape ${selectedBarcode} with LTFS. All existing data will be erased. To confirm, type the barcode:`,
            placeholder: selectedBarcode,
            variant: 'danger',
            onConfirm: async (val) => {
                if (val !== selectedBarcode) {
                    showToast('Barcode mismatch', 'error');
                    return;
                }
                setLoading(true);
                try {
                    const res = await api.formatTape(selectedBarcode);
                    if (res.success) {
                        showToast('Initialization started', 'success');
                        await handleScanLibrary();
                    } else {
                        showToast(res.error || 'Initialization failed', 'error');
                    }
                } catch (e) {
                    showToast('Error initializing', 'error');
                } finally {
                    setLoading(false);
                }
            }
        });
    };

    const handleUnloadTape = () => {
        setConfirmModal({
            isOpen: true,
            title: 'Unload Tape',
            message: 'Are you sure you want to unload the current tape from the drive?',
            variant: 'warning',
            onConfirm: async () => {
                setLoading(true);
                try {
                    const res = await api.unloadTape(0);
                    if (res.success) {
                        showToast('Tape unloaded successfully', 'success');
                        await handleScanLibrary();
                    } else {
                        showToast(res.error || 'Unload failed', 'error');
                    }
                } catch (e) {
                    showToast('Failed to unload tape', 'error');
                } finally {
                    setLoading(false);
                }
            }
        });
    };

    const handleCleanDrive = async () => {
        setConfirmModal({
            isOpen: true,
            title: 'Clean Tape Drive',
            message: 'This will trigger a cleaning cycle on Drive 0. Ensure a cleaning tape is available in the library. Continue?',
            variant: 'info',
            onConfirm: async () => {
                setLoading(true);
                try {
                    const res = await api.cleanDrive(0);
                    if (res.success) {
                        showToast('Drive cleaning started successfully', 'success');
                        await handleScanLibrary();
                    } else {
                        showToast(res.error || 'Cleaning failed', 'error');
                    }
                } catch (e) {
                    showToast('Failed to start cleaning', 'error');
                } finally {
                    setLoading(false);
                }
            }
        });
    };

    const handleImportTape = async () => {
        setLoading(true);
        try {
            const res = await api.importTape();
            if (res.success) {
                showToast(res.data?.message || 'Started tape import from mail slot.', 'success');
                await handleScanLibrary();
            } else {
                showToast(res.error || 'Failed to import tape', 'error');
            }
        } catch (e) {
            showToast('Failed to connect to backend', 'error');
        } finally {
            setLoading(false);
        }
    };

    const handleExportTape = async (barcode: string) => {
        setConfirmModal({
            isOpen: true,
            title: 'Export Tape to Mail Slot',
            message: `Are you sure you want to export tape ${barcode} to the mail slot? Note: This requires an empty mail slot and Mail Slot functionality must be enabled.`,
            variant: 'info',
            onConfirm: async () => {
                setLoading(true);
                try {
                    const res = await api.exportTape(barcode);
                    if (res.success) {
                        showToast(res.data?.message || 'Started tape export to mail slot.', 'success');
                        await handleScanLibrary();
                    } else {
                        showToast(res.error || 'Failed to export tape', 'error');
                    }
                } catch (e) {
                    showToast('Failed to connect to backend', 'error');
                } finally {
                    setLoading(false);
                }
            }
        });
    };

    useEffect(() => {
        if (isDemoMode) {
            setTapes(MOCK_TAPES);
            setSelectedBarcode('A00124L8');
            setLoading(false);
            return;
        }
        const fetchTapes = async () => {
            setLoading(true);
            const res = await api.getTapes();
            if (res.success && res.data) {
                const mapped = res.data.tapes.map(t => ({
                    ...t,
                    barcode: t.barcode,
                    alias: t.alias || t.barcode,
                    location: t.location_type === 'drive'
                        ? `Drive ${(t as any).drive_id ?? (t as any).drive_source_slot ?? 0}`
                        : `Slot ${String(t.slot_number || (t as any).slot || 0).padStart(3, '0')}`,
                    location_type: t.location_type,
                    drive_id: (t as any).drive_id ?? (t as any).drive_source_slot ?? 0,
                    status: t.location_type === 'drive' ? `Drive ${(t as any).drive_id ?? (t as any).drive_source_slot ?? 0}` : t.status === 'available' ? 'Online' : t.status === 'in_use' ? 'Busy' : t.status === 'error' ? 'Error' : 'Offline',
                    capacity: (t.capacity_bytes ?? 0) > 0 ? Math.round((t.used_bytes || 0) / (t.capacity_bytes ?? 1) * 100) : 0,
                    used: (t.used_bytes ?? 0) > 0 ? formatCapacity(t.used_bytes!) : '0.0TB',
                    total: (t.capacity_bytes ?? 0) > 0 ? formatCapacity(t.capacity_bytes!) : '0.0TB',
                    type: 'LTO-8',
                    date: '--',
                    mount_count: t.mount_count || 0,
                    error_count: t.error_count || 0,
                    trust: t.trust_status || 'unknown',
                    is_cleaning: t.is_cleaning_tape || t.barcode?.startsWith('CLN'),
                    is_cleaning_tape: t.is_cleaning_tape || t.barcode?.startsWith('CLN'),
                    cleaning_lives: t.cleaning_remaining_uses
                }));
                setTapes(mapped);
                setError(null);
                if (mapped.length > 0) setSelectedBarcode(mapped[0].barcode);
            } else {
                setError(res.error || 'Failed to communicate with library controller.');
            }
            setLoading(false);
        };
        const fetchStats = async () => {
            const res = await api.getSystemStats();
            if (res.success && res.data) {
                setStats(res.data);
            }
        };
        fetchTapes();
        fetchStats();
    }, [isDemoMode]);



    if (loading) {
        return (
            <div className="flex items-center justify-center h-96">
                <div className="flex flex-col items-center gap-4">
                    <RefreshCw className="size-10 text-primary animate-spin" />
                    <span className="text-muted-foreground font-mono text-sm uppercase tracking-wider">Loading Tapes...</span>
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <ErrorState
                title="Failed to Load Library"
                message={error}
                retry={() => window.location.reload()}
                retryLabel="Retry Connection"
            />
        );
    }

    if (tapes.length === 0) {
        return (
            <EmptyState
                icon={Inbox}
                title="No Tapes in Library"
                message="No tapes found during scan."
                action={handleScanLibrary}
                actionLabel="Force Rescan"
            />
        );
    }

    return (
        <div className="flex h-full overflow-hidden bg-[#09090b] relative -m-6">
            {/* Subtle Scanline Overlay */}
            <div className="scanline pointer-events-none absolute inset-0 z-50 opacity-10"></div>

            {/* Main Content Area */}
            <div className="flex-1 flex flex-col min-w-0">
                {/* Internal Header/Toolbar */}
                <div className="flex shrink-0 items-center justify-between border-b border-[#27272a] bg-[#09090b] px-6 py-4 z-10">
                    <div className="flex items-center gap-4">
                        <h1 className="text-xl font-semibold tracking-tight text-white flex items-center gap-2">
                            <CassetteTape className="text-primary size-6" />
                            Tape Inventory
                        </h1>
                        <div className="h-6 w-px bg-[#27272a] mx-2"></div>
                        <div className="text-[10px] text-[#71717a] font-mono uppercase tracking-widest">LIBRARY_ID: FS-9942-X</div>
                    </div>
                    <div className="flex items-center gap-4">
                        <div className="relative group">
                            <Search className="absolute left-3 top-2.5 text-[#71717a] size-4" />
                            <input
                                className="h-9 w-64 rounded-md border border-[#27272a] bg-[#18181b] pl-10 pr-4 text-xs text-white placeholder-[#71717a] focus:border-primary focus:ring-1 focus:ring-primary transition-all outline-none"
                                placeholder="Search barcode, alias..."
                                type="text"
                                value={searchQuery}
                                onChange={(e) => { setSearchQuery(e.target.value); setCurrentPage(1); }}
                            />
                        </div>
                        <button
                            onClick={handleScanLibrary}
                            disabled={scanning || isDemoMode}
                            className="flex items-center gap-2 rounded-md border border-primary/50 px-4 py-2 text-[10px] font-bold text-primary hover:bg-primary/10 transition-colors uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {scanning ? <RefreshCw className="size-4 animate-spin" /> : <ScanBarcode className="size-4" />}
                            {scanning ? 'Scanning...' : 'Scan Library'}
                        </button>

                        <button
                            onClick={handleCleanDrive}
                            disabled={scanning || loading}
                            className="flex items-center gap-2 rounded-md border border-orange-500/50 px-4 py-2 text-[10px] font-bold text-orange-500 hover:bg-orange-500/10 transition-colors uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                            title="Clean Drive 0"
                        >
                            <span className="material-symbols-outlined text-[16px]">cleaning_services</span>
                            Clean
                        </button>

                        <button
                            onClick={handleImportTape}
                            disabled={scanning || loading || !stats?.mailslot_enabled}
                            className="flex items-center gap-2 rounded-md border border-indigo-500/50 px-4 py-2 text-[10px] font-bold text-indigo-500 hover:bg-indigo-500/10 transition-colors uppercase tracking-wider disabled:opacity-50 disabled:cursor-not-allowed"
                            title={stats?.mailslot_enabled ? "Import tape from mail slot" : "Mail Slot is disabled in Service Configuration"}
                        >
                            <span className="material-symbols-outlined text-[16px]">move_to_inbox</span>
                            Import Tape
                        </button>

                        {/* Drive-Only Mode Manual Load */}
                        {stats?.library_info?.drive_only && (
                            <div className="flex items-center gap-2 pl-2 border-l border-[#27272a]">
                                <div className="relative">
                                    <QrCode className="absolute left-2 top-2.5 text-[#71717a] size-3.5" />
                                    <input
                                        className="h-9 w-40 rounded-md border border-[#27272a] bg-[#18181b] pl-8 pr-3 text-xs text-white placeholder-[#71717a] focus:border-primary focus:ring-1 focus:ring-primary transition-all outline-none uppercase font-mono"
                                        placeholder="MANUAL BARCODE"
                                        type="text"
                                        value={manualBarcode}
                                        onChange={(e) => setManualBarcode(e.target.value.toUpperCase())}
                                        onKeyDown={(e) => e.key === 'Enter' && handleManualLoad()}
                                    />
                                </div>
                                <button
                                    onClick={handleManualLoad}
                                    disabled={!manualBarcode || manualLoading}
                                    className="h-9 px-3 rounded-md bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 text-xs font-bold uppercase disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {manualLoading ? 'Loading...' : 'Load'}
                                </button>
                            </div>
                        )}
                    </div>
                </div>

                {/* Filters Row */}
                <div className="flex shrink-0 items-center justify-between border-b border-[#27272a] bg-[#121214] px-6 py-3 z-10">
                    <div className="flex items-center gap-3">
                        <select className="h-8 rounded-md border border-[#27272a] bg-[#18181b] px-3 text-[10px] font-bold uppercase tracking-wider text-[#e4e4e7] focus:border-primary focus:ring-0 outline-none">
                            <option>All Statuses</option>
                            <option>Online</option>
                            <option>Offline</option>
                        </select>
                        <select className="h-8 rounded-md border border-[#27272a] bg-[#18181b] px-3 text-[10px] font-bold uppercase tracking-wider text-[#e4e4e7] focus:border-primary focus:ring-0 outline-none">
                            <option>All Media Types</option>
                            <option>LTO-9</option>
                            <option>LTO-8</option>
                        </select>
                        <div className="h-4 w-px bg-[#27272a] mx-2"></div>
                        <button className="text-[10px] font-bold text-primary hover:underline flex items-center gap-1 uppercase tracking-wider">
                            <RefreshCw className="size-3.5" />
                            Refresh
                        </button>
                    </div>
                    <div className="flex items-center gap-2 text-[10px] text-[#71717a] font-mono uppercase tracking-widest">
                        <span>TOTAL: {filteredTapes.length.toLocaleString()}</span>
                        <span className="text-[#27272a]">|</span>
                        <span>CAPACITY: {totalCapacity.toFixed(0)} TB</span>
                    </div>
                </div>

                {/* Table Area */}
                <div className="flex-1 overflow-auto bg-[#09090b] custom-scrollbar">
                    <table className="w-full text-left text-xs border-collapse">
                        <thead className="sticky top-0 z-10 bg-[#18181b] border-b border-[#27272a] text-[10px] font-bold uppercase tracking-[0.15em] text-[#71717a]">
                            <tr>
                                <th className="px-6 py-4">Barcode</th>
                                <th className="px-6 py-4">Alias</th>
                                <th className="px-6 py-4">Location</th>
                                <th className="px-6 py-4">Status</th>
                                <th className="px-6 py-4">Trust</th>
                                <th className="px-6 py-4">Mounts</th>
                                <th className="px-6 py-4">Errors</th>
                                <th className="px-6 py-4 w-32">Capacity</th>
                                <th className="px-6 py-4 text-right pr-6">Last Access</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-[#27272a] bg-[#09090b]">
                            {paginatedTapes.map((tape) => (
                                <tr
                                    key={tape.barcode}
                                    onClick={() => setSelectedBarcode(tape.barcode)}
                                    className={cn(
                                        "group cursor-pointer transition-all border-l-2",
                                        selectedBarcode === tape.barcode
                                            ? "bg-primary/5 hover:bg-primary/10 border-l-primary"
                                            : "hover:bg-white/5 border-l-transparent"
                                    )}
                                >
                                    <td className="px-6 py-4 font-mono text-primary font-bold">
                                        <div className="flex items-center gap-2">
                                            <QrCode className="size-3.5 opacity-40" />
                                            {tape.barcode}
                                        </div>
                                    </td>
                                    <td className="px-6 py-4 text-[#e4e4e7] font-bold tracking-wide uppercase text-[10px]">{tape.alias}</td>
                                    <td className="px-6 py-4 font-mono text-[10px] text-[#a1a1aa]">{tape.location}</td>
                                    <td className="px-6 py-4">
                                        <span className={cn(
                                            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[9px] font-bold tracking-widest uppercase border",
                                            tape.status.startsWith('Drive') || tape.status === 'Online' ? "bg-primary/10 text-primary border-primary/20" :
                                                tape.status === 'Busy' ? "bg-warning/10 text-warning border-warning/20" :
                                                    tape.status === 'Error' ? "bg-destructive/10 text-destructive border-destructive/20" :
                                                        "bg-[#27272a] text-[#71717a] border-transparent"
                                        )}>
                                            <div className={cn(
                                                "h-1 w-1 rounded-full",
                                                tape.status.startsWith('Drive') || tape.status === 'Online' ? "bg-primary animate-pulse" :
                                                    tape.status === 'Busy' ? "bg-warning animate-spin" :
                                                        tape.status === 'Error' ? "bg-destructive" : "bg-[#71717a]"
                                            )}></div>
                                            {tape.status}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4">
                                        <div className="flex items-center gap-1.5">
                                            {tape.trust === 'trusted' ? <ShieldCheck className="size-3.5 text-primary" /> :
                                                tape.trust === 'partial' ? <Shield className="size-3.5 text-warning" /> :
                                                    tape.trust === 'untrusted' ? <ShieldAlert className="size-3.5 text-destructive" /> : <HelpCircle className="size-3.5 text-[#71717a]" />}
                                            <span className="text-[10px] font-mono uppercase text-[#71717a]">{tape.trust || 'UNK'}</span>
                                        </div>
                                    </td>
                                    <td className="px-6 py-4 font-mono text-[10px] text-[#71717a]">{tape.mount_count || 0}</td>
                                    <td className={cn(
                                        "px-6 py-4 font-mono text-[10px]",
                                        (tape.error_count || 0) > 0 ? "text-destructive font-bold" : "text-[#71717a]"
                                    )}>{tape.error_count || 0}</td>
                                    <td className="px-6 py-4">
                                        {tape.is_cleaning ? (
                                            <div className="flex flex-col gap-1.5">
                                                <div className="flex justify-between text-[9px] text-orange-500 font-mono">
                                                    <span>{tape.cleaning_lives ?? '--'} USES LEFT</span>
                                                    <span>CLEANING</span>
                                                </div>
                                                <div className="h-1 w-full overflow-hidden rounded-full bg-zinc-900 border border-[#27272a]">
                                                    <div
                                                        className="h-full rounded-full bg-orange-500"
                                                        style={{ width: `${Math.min(100, ((tape.cleaning_lives || 0) / 50) * 100)}%` }}
                                                    ></div>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="flex flex-col gap-1.5">
                                                <div className="flex justify-between text-[9px] text-[#71717a] font-mono">
                                                    <span>{tape.used}</span>
                                                    <span>{tape.total}</span>
                                                </div>
                                                <div className="h-1 w-full overflow-hidden rounded-full bg-zinc-900 border border-[#27272a]">
                                                    <div
                                                        className={cn(
                                                            "h-full rounded-full transition-all duration-500",
                                                            tape.status === 'Error' ? "bg-destructive" :
                                                                tape.status === 'Busy' ? "bg-warning" : "bg-primary"
                                                        )}
                                                        style={{ width: `${tape.capacity}%` }}
                                                    ></div>
                                                </div>
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-6 py-4 text-right font-mono text-[10px] text-[#71717a] pr-6">{tape.date}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>

                    {/* Pagination Controls */}
                    {filteredTapes.length > pageSize && (
                        <div className="sticky bottom-0 flex items-center justify-between border-t border-[#27272a] bg-[#121214] px-6 py-3">
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] text-[#71717a] font-mono uppercase tracking-wider">Per Page:</span>
                                <select
                                    value={pageSize}
                                    onChange={(e) => { setPageSize(Number(e.target.value)); setCurrentPage(1); }}
                                    className="h-7 rounded border border-[#27272a] bg-[#18181b] px-2 text-[10px] font-mono text-white focus:border-primary outline-none"
                                >
                                    <option value={25}>25</option>
                                    <option value={50}>50</option>
                                    <option value={100}>100</option>
                                    <option value={250}>250</option>
                                </select>
                            </div>
                            <div className="flex items-center gap-1">
                                <button
                                    onClick={() => setCurrentPage(1)}
                                    disabled={currentPage === 1}
                                    className="h-7 w-7 rounded border border-[#27272a] bg-[#18181b] text-[#71717a] hover:text-white hover:border-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
                                >
                                    <ChevronsLeft className="size-4" />
                                </button>
                                <button
                                    onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                                    disabled={currentPage === 1}
                                    className="h-7 w-7 rounded border border-[#27272a] bg-[#18181b] text-[#71717a] hover:text-white hover:border-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
                                >
                                    <ChevronLeft className="size-4" />
                                </button>
                                <span className="px-3 text-[10px] font-mono text-white">
                                    Page {currentPage} of {totalPages}
                                </span>
                                <button
                                    onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                                    disabled={currentPage === totalPages}
                                    className="h-7 w-7 rounded border border-[#27272a] bg-[#18181b] text-[#71717a] hover:text-white hover:border-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
                                >
                                    <ChevronRight className="size-4" />
                                </button>
                                <button
                                    onClick={() => setCurrentPage(totalPages)}
                                    disabled={currentPage === totalPages}
                                    className="h-7 w-7 rounded border border-[#27272a] bg-[#18181b] text-[#71717a] hover:text-white hover:border-primary disabled:opacity-30 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
                                >
                                    <ChevronsRight className="size-4" />
                                </button>
                            </div>
                            <div className="text-[10px] text-[#71717a] font-mono">
                                Showing {((currentPage - 1) * pageSize) + 1}-{Math.min(currentPage * pageSize, filteredTapes.length)} of {filteredTapes.length.toLocaleString()}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Right Slide-out Panel (ContextPanel) */}
            <ContextPanel
                tape={selectedTape}
                isOpen={!!selectedTape}
                onClose={() => setSelectedBarcode(null)}
                onRestore={() => setShowRestoreWizard(true)}
                onUnload={handleUnloadTape}
                onExport={handleExportTape}
                mailSlotAvailable={!!stats?.mailslot_enabled && (stats?.mailslot_count ?? 0) > 0}
                onInitialize={(barcode) => {
                    if (barcode === selectedBarcode) handleInitializeTape();
                }}
            />

            <ConfirmationModal
                isOpen={confirmModal.isOpen}
                onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
                onConfirm={confirmModal.onConfirm}
                title={confirmModal.title}
                message={confirmModal.message}
                variant={confirmModal.variant}
            />

            <PromptModal
                isOpen={promptModal.isOpen}
                onClose={() => setPromptModal({ ...promptModal, isOpen: false })}
                onConfirm={promptModal.onConfirm}
                title={promptModal.title}
                message={promptModal.message}
                placeholder={promptModal.placeholder}
                variant={promptModal.variant}
            />
            {/* Restore Wizard */}
            <RestoreWizard
                isOpen={showRestoreWizard}
                onClose={() => setShowRestoreWizard(false)}
                onSuccess={() => {
                    // Logic to refresh jobs or show notification
                }}
            />
        </div>
    )
}

function cn(...inputs: any[]) {
    return inputs.filter(Boolean).join(' ');
}
