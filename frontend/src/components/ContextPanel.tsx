import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Tape, api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useDemoMode } from "@/lib/demoMode";

type EraseMode = 'quick' | 'format' | 'secure';

interface ContextPanelProps {
    tape: Tape | null;
    isOpen: boolean;
    onClose: () => void;
    onRestore?: () => void;
    onInitialize?: (barcode: string) => void; // kept for compat, unused
    onUnload?: () => void;
    onLock?: (barcode: string) => void;
    onExport?: (barcode: string) => void;
    mailSlotAvailable?: boolean;
    showViewAction?: boolean;
}

const MOCK_MANIFEST = [
    { name: 'financial_report_2023.pdf', size: '4.2MB', icon: 'description' },
    { name: 'project_backup.tar.gz', size: '1.8GB', icon: 'folder_zip' },
    { name: 'database_dump.sql', size: '256MB', icon: 'database' },
    { name: 'media_archive_01.mov', size: '12.4GB', icon: 'movie' },
    { name: 'logs_2023_q4.zip', size: '89MB', icon: 'folder_zip' },
];

export function ContextPanel({
    tape,
    isOpen,
    onClose,
    onUnload,
    onLock,
    onExport,
    mailSlotAvailable = false,
    showViewAction = false
}: ContextPanelProps) {
    const navigate = useNavigate();
    const { isDemoMode } = useDemoMode();
    const [manifest, setManifest] = useState<any[]>([]);
    const [manifestLoading, setManifestLoading] = useState(false);
    const [alerts, setAlerts] = useState<any[]>([]);
    const [alertsLoading, setAlertsLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<'manifest' | 'health'>('manifest');
    const [eraseOpen, setEraseOpen] = useState(false);
    const [eraseMode, setEraseMode] = useState<EraseMode>('quick');
    const [eraseConfirm, setEraseConfirm] = useState('');
    const [erasing, setErasing] = useState(false);

    // Format byte values
    const formatBytes = (bytes: number | undefined) => {
        if (bytes === undefined || bytes === null) return "Unknown";
        if (bytes === 0) return "0 GB";
        if (bytes >= 1024 * 1024 * 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024 * 1024 * 1024)).toFixed(1)} PB`;
        if (bytes >= 1024 * 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024 * 1024)).toFixed(1)} TB`;
        return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    };

    // Fetch manifest when tape changes
    useEffect(() => {
        if (!tape || !isOpen) {
            setManifest([]);
            return;
        }

        // Don't fetch manifest for cleaning media
        if (tape.is_cleaning_tape || tape.barcode.startsWith('CLN')) {
            setManifest([]);
            return;
        }

        const fetchManifest = async () => {
            if (isDemoMode) {
                setManifest(MOCK_MANIFEST);
                return;
            }

            setManifestLoading(true);
            try {
                const res = await api.getTapeManifest(tape.barcode);
                if (res.success && res.data) {
                    // Check if the backend identified it as cleaning media in the response
                    if (res.data.manifest?.is_cleaning_media) {
                        setManifest([]);
                        return;
                    }

                    setManifest((res.data.manifest?.files || []).map((f: any) => ({
                        name: f.filename || f.file_name || f.name || f.file_path || 'Unknown',
                        size: f.size_readable || (f.size ? `${(f.size / (1024 * 1024)).toFixed(1)}MB` : (f.file_size ? `${(f.file_size / (1024 * 1024)).toFixed(1)}MB` : '0B')),
                        icon: (f.filename || f.file_name || '')?.endsWith('.pdf') ? 'description' :
                            (f.filename || f.file_name || '')?.endsWith('.sql') ? 'database' :
                                (f.filename || f.file_name || '')?.match(/\.(zip|tar|gz)$/) ? 'folder_zip' :
                                    (f.filename || f.file_name || '')?.match(/\.(mov|mp4|mkv)$/) ? 'movie' : 'insert_drive_file'
                    })));
                } else {
                    setManifest([]);
                }
            } catch (err) {
                setManifest([]);
            } finally {
                setManifestLoading(false);
            }
        };
        fetchManifest();
    }, [tape?.barcode, isOpen, isDemoMode, tape?.is_cleaning_tape]);

    // Fetch alerts
    useEffect(() => {
        if (!tape || !isOpen || activeTab !== 'health') return;

        const fetchAlerts = async () => {
            if (isDemoMode) {
                setAlerts([
                    { alert_name: 'Media Life Warning', severity: 'warning', message: 'Media has reached 80% of its rated life.', created_at: new Date().toISOString() },
                    { alert_name: 'Read Error Correction', severity: 'info', message: 'Substantial correction used during read.', created_at: new Date(Date.now() - 86400000).toISOString() }
                ]);
                return;
            }

            setAlertsLoading(true);
            try {
                const res = await api.getTapeAlerts(tape.barcode);
                if (res.success && res.data) {
                    setAlerts(res.data.alerts || []);
                }
            } catch (err) {
                console.error("Failed to fetch alerts", err);
            } finally {
                setAlertsLoading(false);
            }
        };

        fetchAlerts();
    }, [tape?.barcode, isOpen, activeTab, isDemoMode]);

    const handleExportManifest = () => {
        if (manifest.length === 0 || !tape) return;

        const headers = ["Filename", "Size"];
        const rows = manifest.map(f => [f.name, f.size]);
        const csvContent = [headers, ...rows].map(e => e.join(",")).join("\n");

        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", `manifest_${tape.barcode}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    };

    if (!tape) return null;

    // Helper to get consistent location string
    const locationStr = tape.location_type === 'drive'
        ? `Drive ${tape.drive_id ?? tape.slot_number ?? 0}`
        : `Slot ${String(tape.slot_number ?? 0).padStart(3, '0')}`;

    return (
        <div className={cn(
            "fixed inset-y-0 right-0 w-[400px] bg-[#121214] border-l border-[#27272a] z-50 transition-transform duration-300 ease-in-out shadow-2xl flex flex-col transform",
            isOpen ? "translate-x-0" : "translate-x-full"
        )}>
            <div className="flex h-16 shrink-0 items-center justify-between border-b border-[#27272a] px-6 bg-[#18181b]/50">
                <h2 className="text-[10px] font-bold text-white uppercase tracking-[0.2em] flex items-center gap-2">
                    <span className="material-symbols-outlined text-primary text-base">analytics</span>
                    Tape Intelligence
                </h2>
                <button onClick={onClose} className="text-[#71717a] hover:text-white transition-colors">
                    <span className="material-symbols-outlined text-lg">close</span>
                </button>
            </div>

            <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
                {/* Summary Header */}
                <div className="mb-6 rounded border border-[#27272a] bg-[#09090b] p-4 relative overflow-hidden group">
                    <div className="absolute -right-6 -top-6 h-24 w-24 rounded-full bg-primary/5 blur-xl group-hover:bg-primary/10 transition-colors"></div>
                    <div className="flex justify-between items-start mb-2 relative z-10">
                        <div>
                            <div className="text-[9px] uppercase text-[#71717a] font-mono font-bold tracking-widest mb-1">Archive ID</div>
                            <div className="text-xl font-bold text-primary font-mono tracking-tighter uppercase">{tape.barcode}</div>
                        </div>
                        <div className="h-10 w-10 min-w-10 rounded bg-[#18181b] flex items-center justify-center text-primary border border-primary/20 shadow-glow">
                            <span className="material-symbols-outlined text-xl">qr_code_2</span>
                        </div>
                    </div>
                    <div className="flex justify-between items-end mb-4 relative z-10">
                        <div className="text-[10px] text-[#e4e4e7] font-bold uppercase tracking-wider">
                            {(tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN') ? 'Cleaning Medium' : (tape.alias || 'No Alias')}
                        </div>
                        {tape.worm_lock && (
                            <div className="flex items-center gap-1.5 px-2 py-0.5 bg-primary/10 border border-primary/20 rounded text-primary text-[8px] font-bold uppercase tracking-widest animate-pulse">
                                <span className="material-symbols-outlined text-[10px]">lock</span>
                                WORM LOCKED
                            </div>
                        )}
                    </div>
                    <div className="grid grid-cols-2 gap-4 border-t border-[#27272a] pt-4 relative z-10">
                        <div>
                            <div className="text-[9px] text-[#71717a] uppercase font-mono font-bold tracking-widest">State</div>
                            <div className={cn(
                                "text-[10px] font-bold uppercase tracking-wider flex items-center gap-1.5 mt-1",
                                tape.location_type === 'drive' ? "text-primary" : (
                                    (tape as any).status === 'offline' || (tape as any).status === 'Offline' ? "text-white" :
                                        (tape as any).status === 'in_use' || (tape as any).status === 'Busy' ? "text-warning" : "text-primary"
                                )
                            )}>
                                <div className={cn(
                                    "size-1.5 rounded-full animate-pulse",
                                    tape.location_type === 'drive' ? "bg-primary" : (
                                        (tape as any).status === 'offline' || (tape as any).status === 'Offline' ? "bg-white/50" :
                                            (tape as any).status === 'in_use' || (tape as any).status === 'Busy' ? "bg-warning" : "bg-primary"
                                    )
                                )}></div>
                                <span className={tape.location_type === 'drive' ? 'text-primary' : ''}>
                                    {tape.location_type === 'drive' ? `Drive ${tape.drive_id || (tape as any).drive_source_slot || 0}` : tape.status}
                                </span>
                            </div>
                        </div>
                        <div>
                            <div className="text-[9px] text-[#71717a] uppercase font-mono font-bold tracking-widest">Position</div>
                            <div className="text-[10px] font-bold text-white uppercase tracking-wider font-mono mt-1">{locationStr}</div>
                        </div>
                    </div>
                </div>

                {/* Quick Actions (Eject) */}
                {tape.location_type === 'drive' && (
                    <div className="grid grid-cols-1 gap-3 mb-6">
                        <button
                            onClick={onUnload}
                            disabled={!onUnload}
                            className="flex items-center justify-center gap-2 rounded border border-primary/30 bg-primary/5 p-3 hover:bg-primary/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                            title="Eject tape from drive"
                        >
                            <span className="material-symbols-outlined text-sm text-primary">eject</span>
                            <span className="text-[10px] font-bold uppercase tracking-wider text-primary">Eject</span>
                        </button>
                    </div>
                )}

                {onLock && !tape.worm_lock && !(tape as any).is_cleaning_tape && !(tape as any).is_cleaning && (
                    <div className="mb-6">
                        <button
                            onClick={() => onLock(tape.barcode)}
                            className="w-full flex items-center justify-center gap-2 rounded border border-primary/30 bg-primary/5 p-3 hover:bg-primary/10 transition-colors text-primary"
                        >
                            <span className="material-symbols-outlined text-sm">lock_open</span>
                            <span className="text-[10px] font-bold uppercase tracking-[0.15em]">Apply WORM Lock</span>
                        </button>
                        <p className="text-[8px] text-[#71717a] mt-2 italic px-2">WARNING: WORM locks are permanent and prevent all data modification or erasure.</p>
                    </div>
                )}

                {/* Metadata Grid */}
                <div className="grid grid-cols-2 gap-3 mb-6">
                    {[
                        {
                            label: 'Trust Status',
                            value: (tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN') ? 'N/A' : (tape.trust_status || (tape as any).trust || 'Unknown').toUpperCase(),
                            sub: 'Verification Level',
                            color: ((tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN')) ? 'text-[#71717a]' : (((tape as any).trust_status || (tape as any).trust) === 'trusted' ? 'text-primary' : ((tape as any).trust_status || (tape as any).trust) === 'untrusted' ? 'text-destructive' : 'text-white')
                        },
                        {
                            label: 'Total Capacity',
                            value: (tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN') ? 'N/A' : formatBytes(tape.capacity_bytes),
                            sub: 'Tape Capacity',
                            color: (tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN') ? 'text-[#71717a]' : 'text-white'
                        },
                        {
                            label: 'Load History',
                            value: (tape.mount_count || 0) + ' Cycles',
                            sub: 'Lifetime',
                            color: 'text-white'
                        },
                        {
                            label: 'Used Data',
                            value: (tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN') ? 'N/A' : formatBytes(tape.used_bytes),
                            sub: 'Occupied Stream',
                            color: (tape as any).is_cleaning_tape || (tape as any).is_cleaning || tape.barcode?.startsWith('CLN') ? 'text-[#71717a]' : 'text-primary'
                        },
                    ].map((m, i) => (
                        <div key={i} className="rounded border border-[#27272a] bg-[#18181b]/50 p-3">
                            <div className="text-[9px] text-[#71717a] uppercase font-mono font-bold tracking-widest mb-1">{m.label}</div>
                            <div className={cn("text-lg font-bold font-mono tracking-tighter", m.color)}>{m.value}</div>
                            <div className="text-[9px] text-[#71717a] font-bold uppercase tracking-wider mt-1">{m.sub}</div>
                        </div>
                    ))}
                </div>

                {/* Tab Switcher */}
                <div className="flex border-b border-[#27272a] mb-4">
                    <button
                        onClick={() => setActiveTab('manifest')}
                        className={cn(
                            "flex-1 py-2 text-[10px] font-bold uppercase tracking-widest transition-all border-b-2",
                            activeTab === 'manifest' ? "border-primary text-primary" : "border-transparent text-[#71717a] hover:text-white"
                        )}
                    >
                        Object Manifest
                    </button>
                    <button
                        onClick={() => setActiveTab('health')}
                        className={cn(
                            "flex-1 py-2 text-[10px] font-bold uppercase tracking-widest transition-all border-b-2",
                            activeTab === 'health' ? "border-primary text-primary" : "border-transparent text-[#71717a] hover:text-white"
                        )}
                    >
                        Hardware Health
                    </button>
                </div>

                {activeTab === 'manifest' ? (
                    <>
                        <div className="flex items-center justify-between mb-3 px-1">
                            <h3 className="text-[10px] font-bold text-white uppercase tracking-[0.15em]">Object Manifest</h3>
                            <button
                                onClick={handleExportManifest}
                                disabled={manifest.length === 0}
                                className="text-[9px] font-bold text-primary uppercase tracking-wider hover:underline disabled:opacity-30 disabled:no-underline"
                            >
                                Export .CSV
                            </button>
                        </div>
                        <div className="rounded overflow-hidden border border-[#27272a] bg-[#09090b]">
                            <div className="bg-[#18181b] px-3 py-2 border-b border-[#27272a] flex justify-between items-center">
                                <div className="text-[9px] font-mono text-[#71717a] truncate uppercase">
                                    {`/root/archive/${tape.barcode}`}
                                </div>
                                <span className="material-symbols-outlined text-[#71717a] text-sm">folder_open</span>
                            </div>
                            <div className="max-h-64 overflow-y-auto custom-scrollbar">
                                {manifestLoading ? (
                                    <div className="p-4 flex justify-center">
                                        <div className="size-4 border-2 border-primary/20 border-t-primary rounded-full animate-spin"></div>
                                    </div>
                                ) : manifest.length === 0 ? (
                                    <div className="p-4 text-center text-[10px] text-[#71717a] uppercase tracking-wider">
                                        {tape.is_cleaning_tape ? 'No Objects (Cleaning Medium)' : 'No objects found'}
                                    </div>
                                ) : (
                                    manifest.map((file, i) => (
                                        <div key={file.name + i} className="px-3 py-2.5 flex items-center justify-between hover:bg-white/5 border-b border-[#27272a]/50 last:border-0 transition-colors">
                                            <div className="flex items-center gap-3 overflow-hidden">
                                                <span className="material-symbols-outlined text-[#71717a] text-[16px]">{file.icon}</span>
                                                <span className="text-[10px] text-[#e4e4e7] font-mono truncate">{file.name}</span>
                                            </div>
                                            <span className="text-[9px] text-[#71717a] font-mono uppercase tracking-widest ml-2">{file.size}</span>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </>
                ) : (
                    <>
                        <div className="flex items-center justify-between mb-3 px-1">
                            <h3 className="text-[10px] font-bold text-white uppercase tracking-[0.15em]">Predictive Reliability</h3>
                            <div className="text-[9px] font-mono text-primary uppercase tracking-widest">Live_Bus_Polling</div>
                        </div>
                        <div className="rounded overflow-hidden border border-[#27272a] bg-[#09090b]">
                            <div className="bg-[#18181b] px-3 py-2 border-b border-[#27272a] flex justify-between items-center">
                                <div className="text-[9px] font-mono text-[#71717a] truncate uppercase">
                                    TapeAlerts (LTO-SPEC 0x2E)
                                </div>
                                <span className="material-symbols-outlined text-[#71717a] text-sm">monitor_heart</span>
                            </div>
                            <div className="max-h-64 overflow-y-auto custom-scrollbar">
                                {alertsLoading ? (
                                    <div className="p-4 flex justify-center">
                                        <div className="size-4 border-2 border-primary/20 border-t-primary rounded-full animate-spin"></div>
                                    </div>
                                ) : alerts.length === 0 ? (
                                    <div className="p-8 text-center">
                                        <div className="flex justify-center mb-3">
                                            <div className="size-10 rounded-full bg-primary/5 flex items-center justify-center border border-primary/10">
                                                <span className="material-symbols-outlined text-primary text-xl">check_circle</span>
                                            </div>
                                        </div>
                                        <div className="text-[10px] text-white font-bold uppercase tracking-wider mb-1">Hardware Nominal</div>
                                        <div className="text-[9px] text-[#71717a] uppercase tracking-widest leading-loose">No critical or warning alerts detected on this medium.</div>
                                    </div>
                                ) : (
                                    alerts.map((alert, i) => (
                                        <div key={i} className="px-4 py-3 border-b border-[#27272a]/50 last:border-0 hover:bg-white/5 transition-colors">
                                            <div className="flex items-start gap-3">
                                                <div className={cn(
                                                    "mt-0.5",
                                                    alert.severity === 'critical' ? "text-destructive" :
                                                        alert.severity === 'warning' ? "text-warning" : "text-[#71717a]"
                                                )}>
                                                    <span className="material-symbols-outlined text-lg">
                                                        {alert.severity === 'critical' ? 'report' : alert.severity === 'warning' ? 'warning' : 'info'}
                                                    </span>
                                                </div>
                                                <div className="flex-1">
                                                    <div className="flex justify-between items-start">
                                                        <div className="text-[10px] font-bold text-white uppercase tracking-tight">{alert.alert_name}</div>
                                                        <div className="text-[8px] font-mono text-[#71717a]">
                                                            {new Date(alert.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                                                        </div>
                                                    </div>
                                                    <div className="text-[9px] text-[#71717a] leading-relaxed mt-1">{alert.message}</div>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </>
                )}

                {/* Action Stack */}
                <div className="mt-8 flex flex-col gap-3">
                    {showViewAction && (
                        <button
                            onClick={() => navigate('/tapes')}
                            className="w-full py-3 bg-[#27272a] hover:bg-[#3f3f46] text-white text-[10px] font-bold uppercase tracking-wider rounded transition-all flex items-center justify-center gap-2"
                        >
                            <span className="material-symbols-outlined text-sm">visibility</span>
                            View in Tapes Page
                        </button>
                    )}

                    {onExport && mailSlotAvailable && tape.location_type !== 'drive' && (
                        <button
                            onClick={() => onExport(tape.barcode)}
                            className="w-full flex items-center justify-center gap-2 rounded border border-primary/30 bg-primary/5 p-3 hover:bg-primary/10 transition-colors text-primary"
                        >
                            <span className="material-symbols-outlined text-sm">move_up</span>
                            <span className="text-[10px] font-bold uppercase tracking-[0.15em]">Export to Mail Slot</span>
                        </button>
                    )}

                    {!tape.is_cleaning_tape && (
                        <button
                            onClick={() => { setEraseOpen(true); setEraseMode('quick'); setEraseConfirm(''); }}
                            className="w-full rounded bg-transparent border border-destructive/30 px-4 py-3 text-[10px] font-bold text-destructive uppercase tracking-[0.2em] hover:bg-destructive/10 hover:border-destructive transition-all mt-2 flex items-center justify-center gap-2"
                        >
                            <span className="material-symbols-outlined text-sm">format_underlined</span>
                            Erase / Format
                        </button>
                    )}
                </div>
            </div>

            {/* Erase Mode Modal */}
            {eraseOpen && tape && (
                <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[60] flex items-center justify-center p-4" onClick={() => setEraseOpen(false)}>
                    <div className="bg-[#121214] border border-[#27272a] rounded-lg w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
                        <div className="p-4 border-b border-[#27272a] flex items-center gap-2">
                            <span className="material-symbols-outlined text-destructive text-lg">warning</span>
                            <h3 className="text-white text-sm font-bold uppercase tracking-widest">Erase {tape.barcode}</h3>
                        </div>
                        <div className="p-4 space-y-2">
                            <p className="text-[11px] text-[#71717a] uppercase tracking-wider font-bold mb-3">Select Erase Mode</p>
                            {([
                                { value: 'quick' as EraseMode, label: 'Quick Erase', time: '~5 sec', desc: 'Writes EOD marker at BOT. Data still recoverable.', icon: 'bolt' },
                                { value: 'format' as EraseMode, label: 'Reformat (LTFS)', time: '~30 sec', desc: 'Fresh LTFS filesystem. Ready for immediate use.', icon: 'format_paint' },
                                { value: 'secure' as EraseMode, label: 'Secure Erase', time: '4-10 hrs', desc: 'Full physical overwrite. Forensically unrecoverable.', icon: 'shield_lock' },
                            ]).map(opt => (
                                <button key={opt.value} onClick={() => setEraseMode(opt.value)}
                                    className={`w-full text-left p-3 rounded-md border transition-all ${eraseMode === opt.value ? 'border-primary bg-primary/10 text-white' : 'border-[#27272a] bg-[#18181b] text-[#a1a1aa] hover:border-[#3f3f46]'
                                        }`}>
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-2">
                                            <span className={`material-symbols-outlined text-base ${eraseMode === opt.value ? 'text-primary' : 'text-[#52525b]'}`}>{opt.icon}</span>
                                            <span className="text-xs font-bold uppercase tracking-wider">{opt.label}</span>
                                        </div>
                                        <span className={`text-[10px] font-mono ${eraseMode === opt.value ? 'text-primary' : 'text-[#52525b]'}`}>{opt.time}</span>
                                    </div>
                                    <p className="text-[10px] text-[#71717a] mt-1 ml-7">{opt.desc}</p>
                                </button>
                            ))}
                        </div>
                        <div className="p-4 border-t border-[#27272a]">
                            <p className="text-[10px] text-destructive font-mono mb-2">Type "{tape.barcode}" to confirm.</p>
                            <form onSubmit={async e => {
                                e.preventDefault();
                                if (eraseConfirm !== tape.barcode) return;
                                setErasing(true);
                                try {
                                    const res = await api.wipeTape(tape.barcode, 0, eraseMode);
                                    if (!res.success) console.error('Erase failed:', res.error);
                                } catch (err: any) { console.error(err); }
                                finally { setErasing(false); setEraseOpen(false); setEraseConfirm(''); }
                            }}>
                                <div className="flex gap-2">
                                    <input
                                        type="text" value={eraseConfirm} onChange={e => setEraseConfirm(e.target.value)}
                                        placeholder="Type barcode..." autoFocus
                                        className="flex-1 bg-[#18181b] border border-[#27272a] rounded px-3 py-2 text-white text-xs font-mono placeholder:text-[#3f3f46] focus:outline-none focus:border-primary"
                                    />
                                    <button type="submit" disabled={eraseConfirm !== tape.barcode || erasing}
                                        className="px-4 py-2 bg-destructive hover:bg-destructive/80 text-white rounded text-[10px] font-bold uppercase tracking-wider transition-colors disabled:opacity-40">
                                        {erasing ? '...' : 'Confirm'}
                                    </button>
                                    <button type="button" onClick={() => setEraseOpen(false)}
                                        className="px-3 py-2 text-[#71717a] hover:text-white text-[10px] font-bold uppercase">
                                        Cancel
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// Add this CSS to your globals or component:
// .custom-scrollbar::-webkit-scrollbar { width: 4px; }
// .custom-scrollbar::-webkit-scrollbar-track { bg: #09090b; }
// .custom-scrollbar::-webkit-scrollbar-thumb { bg: #27272a; rounded: 4px; }
