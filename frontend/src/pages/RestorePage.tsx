import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../lib/api';
import { useDemoMode } from '../lib/demoMode';
import { useSocket } from '../contexts/SocketProvider';
import { Checkbox } from '../components/ui/Checkbox';
import { cn } from '../lib/utils';

const MOCK_FILES = [
    { name: 'database_backup_2023.sql', size: '450.0 GB', date: 'Oct 15, 2023', tapeId: 'LTO-8-0042', icon: 'database', selected: true, isEncrypted: true },
    { name: 'project_alpha_v2.tar.gz', size: '120.0 GB', date: 'Sep 01, 2023', tapeId: 'LTO-8-0039', icon: 'folder_zip', selected: false },
    { name: 'render_final_4k_master.mov', size: '82.4 GB', date: 'Aug 20, 2023', tapeId: 'LTO-8-0042', icon: 'movie', selected: true },
    { name: 'contract_scan_legacy.pdf', size: '1.2 GB', date: 'Jul 11, 2023', tapeId: 'LTO-8-0035', icon: 'description', selected: false },
    { name: 'logs_2022_Q4.zip', size: '45.0 GB', date: 'Jan 02, 2023', tapeId: 'LTO-8-0042', icon: 'folder_zip', selected: true },
];

export default function RestorePage() {
    const { isDemoMode } = useDemoMode();
    const { socket } = useSocket();

    const [files, setFiles] = useState<any[]>(isDemoMode ? MOCK_FILES : []);
    const [loading, setLoading] = useState(!isDemoMode);
    const [searchQuery, setSearchQuery] = useState('');
    const [wizardStep, setWizardStep] = useState<'selection' | 'destination' | 'options' | 'confirm'>('selection');

    // Filters
    const [selectedTape, setSelectedTape] = useState<string>('all');
    const [selectedExtension, setSelectedExtension] = useState<string>('all');
    const [availableTapes, setAvailableTapes] = useState<string[]>([]);
    const [availableExtensions] = useState<string[]>(['.bin', '.txt', '.sql', '.tar', '.gz']);

    // Restore Options
    const [restoring, setRestoring] = useState(false);
    const [selectedDestination, setSelectedDestination] = useState<any>(null);
    const [customDestination, setCustomDestination] = useState('/var/lib/fossilsafe/restore');
    const [mounts, setMounts] = useState<any[]>([]);
    const [decryptionPassword, setDecryptionPassword] = useState('');
    const [simulationMode, setSimulationMode] = useState(false);

    const [totalSlots, setTotalSlots] = useState<number>(0);
    const [showModal, setShowModal] = useState(false);
    const [tapeLoadRequest, setTapeLoadRequest] = useState<{ barcode: string; drive: string } | null>(null);

    const [verificationResult, setVerificationResult] = useState<any>(null);

    useEffect(() => {
        const fetchData = async () => {
            if (isDemoMode) {
                setTotalSlots(24);
                setAvailableTapes(['LTO-8-0042', 'LTO-8-0039', 'LTO-8-0035']);
                setMounts([
                    { id: 'usb_1', name: 'USB Drive: SAN-2GB', path: '/mnt/usb/SAN2GB', type: 'usb' },
                    { id: 'local_default', name: 'Local Recovery Folder', path: '/var/lib/fossilsafe/restore', type: 'local' }
                ]);
                return;
            }

            // Stats
            api.getSystemStats().then(res => {
                if (res.success && res.data) setTotalSlots(res.data.total_slots);
            });

            // Tapes for filter
            api.getTapes().then(res => {
                if (res.success && res.data) {
                    setAvailableTapes(res.data.tapes.map((t: any) => t.barcode));
                }
            });

            // Mounts for wizard
            api.getSystemMounts().then(res => {
                if (res.success && res.data) {
                    setMounts(res.data.mounts);
                    // Default to local if available
                    const local = res.data.mounts.find((m: any) => m.type === 'local');
                    if (local) setSelectedDestination(local);
                }
            });
        };
        fetchData();
    }, [isDemoMode]);

    useEffect(() => {
        if (!socket) return;
        socket.on('restore_tape_needed', (data: { barcode: string; drive_id?: string }) => {
            if (totalSlots <= 1) {
                setTapeLoadRequest({ barcode: data.barcode, drive: data.drive_id || 'DRIVE_A' });
                setShowModal(true);
            }
        });
        return () => { socket.off('restore_tape_needed'); };
    }, [socket, totalSlots]);

    const [selectedFiles, setSelectedFiles] = useState<any[]>([]);

    const fetchFiles = useCallback(async (query: string, tape: string, ext: string) => {
        if (isDemoMode) {
            let filtered = MOCK_FILES.filter(f => f.name.toLowerCase().includes(query.toLowerCase()));
            if (tape !== 'all') filtered = filtered.filter(f => f.tapeId === tape);
            
            setFiles(filtered.map(f => ({
                ...f,
                id: Math.random() * 1000, // or some stable ID if MOCK_FILES had them
                isDuplicate: filtered.filter(f2 => f2.name === f.name).length > 1,
            })));
            setLoading(false);
            return;
        }

        setLoading(true);
        const searchParams: any = { query, limit: 100 };
        if (tape !== 'all') searchParams.tape_barcode = tape;
        if (ext !== 'all') searchParams.extension = ext;

        try {
            const res = await api.searchFiles(searchParams);
            if (res.success && res.data) {
                const fetchedFiles = res.data.files;
                
                // Track seen checksums to only label subsequent ones as duplicates
                const seenChecksums = new Set<string>();

                setFiles(fetchedFiles.map((f: any) => {
                    const isDuplicate = f.checksum ? seenChecksums.has(f.checksum) : false;
                    if (f.checksum) seenChecksums.add(f.checksum);

                    return {
                        id: f.id,
                        name: f.filename || f.file_name || f.name || f.file_path,
                        size: f.size_readable || (f.size ? `${(f.size / (1024 * 1024 * 1024)).toFixed(1)} GB` : '0.0 GB'),
                        date: new Date(f.created_at || f.archived_at || Date.now()).toLocaleDateString('en-US', { month: 'short', day: '2-digit', year: 'numeric' }),
                        tapeId: f.tape_barcode || f.barcode || 'UNK',
                        isEncrypted: f.is_encrypted,
                        checksum: f.checksum,
                        isDuplicate: isDuplicate,
                        icon: (f.filename || f.file_name || '')?.endsWith('.sql') ? 'database' : (f.filename || f.file_name || '')?.endsWith('.zip') || (f.filename || f.file_name || '')?.endsWith('.gz') ? 'folder_zip' : 'description',
                    };
                }));
            }
        } catch (err) {
            console.error("Search failed:", err);
        }
        setLoading(false);
    }, [isDemoMode]);

    useEffect(() => {
        const timer = setTimeout(() => {
            fetchFiles(searchQuery, selectedTape, selectedExtension);
        }, 500);
        return () => clearTimeout(timer);
    }, [searchQuery, selectedTape, selectedExtension, fetchFiles]);

    const totalSizeBytes = selectedFiles.reduce((acc, f) => {
        const val = parseFloat(f.size);
        const unit = f.size.split(' ')[1];
        if (unit === 'GB') return acc + val * 1024 * 1024 * 1024;
        if (unit === 'MB') return acc + val * 1024 * 1024;
        return acc + val;
    }, 0);
    const totalSize = `${(totalSizeBytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;

    const toggleFile = (file: any) => {
        setSelectedFiles(prev => {
            const exists = prev.find(f => f.id === file.id);
            if (exists) {
                return prev.filter(f => f.id !== file.id);
            } else {
                return [...prev, file];
            }
        });
    };

    const handleToggleAll = (checked: boolean) => {
        if (checked) {
            setSelectedFiles(prev => {
                const newItems = files.filter(f => !prev.some(p => p.id === f.id));
                return [...prev, ...newItems];
            });
        } else {
            setSelectedFiles(prev => prev.filter(p => !files.some(f => f.id === p.id)));
        }
    };

    const allSelected = files.length > 0 && files.every(f => selectedFiles.some(sf => sf.id === f.id));

    return (
        <div className="flex h-full overflow-hidden bg-[#09090b] relative -m-6">
            <div className="flex flex-1 overflow-hidden">
                {/* Left Pane: Search & Browse */}
                <div className="flex-1 flex flex-col border-r border-[#27272a] min-w-[500px]">
                    <div className="p-4 border-b border-[#27272a] bg-[#121214]/50 flex flex-col gap-4">
                        <div className="relative group">
                            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-[#71717a] group-focus-within:text-primary transition-colors">
                                <span className="material-symbols-outlined text-lg">search</span>
                            </div>
                            <input
                                className="block w-full rounded border-[#27272a] bg-black/40 pl-10 pr-4 py-2.5 text-xs text-white placeholder-[#71717a] focus:border-primary/50 focus:ring-0 transition-all font-mono outline-none"
                                placeholder="Search archive by filename..."
                                type="text"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                        </div>
                        <div className="flex gap-2">
                            <select
                                value={selectedTape}
                                onChange={(e) => setSelectedTape(e.target.value)}
                                className="bg-[#18181b] border border-[#27272a] text-[#71717a] text-[9px] font-bold uppercase tracking-wider px-3 py-1.5 rounded outline-none focus:border-primary/40"
                            >
                                <option value="all">All Tapes</option>
                                {availableTapes.map(t => <option key={t} value={t}>{t}</option>)}
                            </select>
                            <select
                                value={selectedExtension}
                                onChange={(e) => setSelectedExtension(e.target.value)}
                                className="bg-[#18181b] border border-[#27272a] text-[#71717a] text-[9px] font-bold uppercase tracking-wider px-3 py-1.5 rounded outline-none focus:border-primary/40"
                            >
                                <option value="all">Any Extension</option>
                                {availableExtensions.map(e => <option key={e} value={e}>{e.toUpperCase()}</option>)}
                            </select>
                            <button className="flex items-center gap-2 px-3 py-1.5 rounded bg-[#18181b] border border-[#27272a] text-[#71717a] text-[9px] font-bold uppercase tracking-wider transition-all">
                                <span>Recent: 30D</span>
                                <span className="material-symbols-outlined text-[14px]">expand_more</span>
                            </button>
                        </div>
                    </div>

                    <div className="grid grid-cols-[40px_1fr_100px_140px_120px] gap-2 px-4 py-2 border-b border-[#27272a] bg-[#18181b] text-[9px] font-bold uppercase tracking-widest text-[#71717a] select-none sticky top-0 z-10">
                        <div className="flex items-center justify-center">
                            <Checkbox checked={allSelected} onChange={handleToggleAll} />
                        </div>
                        <div className="flex items-center">File Name</div>
                        <div className="flex items-center justify-end">Size</div>
                        <div className="flex items-center pl-4">Archived</div>
                        <div className="flex items-center pl-2">Tape ID</div>
                    </div>

                    <div className="flex-1 overflow-y-auto bg-[#09090b] custom-scrollbar">
                        {loading ? (
                            <div className="flex flex-col items-center justify-center h-full gap-4 text-[#71717a]">
                                <div className="size-8 rounded-full border-2 border-primary/20 border-t-primary animate-spin"></div>
                                <span className="text-[10px] font-bold uppercase tracking-widest">Searching...</span>
                            </div>
                        ) : files.length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-full text-[#71717a]">
                                <span className="material-symbols-outlined text-4xl mb-2 opacity-20">search_off</span>
                                <span className="text-[10px] font-bold uppercase tracking-widest">No objects found</span>
                            </div>
                        ) : (
                            <table className="w-full text-left border-collapse">
                                <tbody className="divide-y divide-[#27272a] text-xs">
                                    {files.map((file) => {
                                        const isSelected = selectedFiles.some(sf => sf.id === file.id);
                                        return (
                                            <tr 
                                                key={file.id} 
                                                onClick={() => toggleFile(file)} 
                                                className={cn(
                                                    "group cursor-pointer transition-colors border-b border-[#27272a]/50", 
                                                    isSelected ? "bg-primary/[0.04] hover:bg-primary/[0.08]" : "hover:bg-white/[0.02]"
                                                )}
                                            >
                                                <td className="p-3 w-[40px] text-center" onClick={(e) => e.stopPropagation()}>
                                                    <Checkbox checked={isSelected} onChange={() => toggleFile(file)} />
                                                </td>
                                                <td className="p-3 text-[#e4e4e7] font-mono group-hover:text-primary transition-colors flex items-center gap-3">
                                                    <span className="material-symbols-outlined text-[#71717a] text-[18px]">{file.icon}</span>
                                                    <div className="flex flex-col min-w-0">
                                                        <div className="flex items-center gap-2">
                                                            <span className="font-bold tracking-tight truncate max-w-xs">{file.name}</span>
                                                            {file.isDuplicate && (
                                                                <span className="px-1.5 py-0.5 rounded-[2px] bg-orange-500/10 border border-orange-500/20 text-orange-400 text-[8px] font-bold uppercase tracking-widest whitespace-nowrap">Duplicate</span>
                                                            )}
                                                        </div>
                                                        {file.checksum && (
                                                            <span className="text-[8px] text-[#3f3f46] font-mono truncate">{file.checksum.substring(0, 32)}...</span>
                                                        )}
                                                    </div>
                                                </td>
                                                <td className="p-3 text-right font-mono text-[#71717a] whitespace-nowrap">{file.size}</td>
                                                <td className="p-3 text-[#71717a] whitespace-nowrap pl-6 font-mono text-[10px] uppercase">{file.date}</td>
                                                <td className="p-3">
                                                    <span className={cn(
                                                        "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono border whitespace-nowrap", 
                                                        isSelected ? "bg-primary/10 text-primary border-primary/20" : "bg-[#18181b] border-[#27272a] text-[#71717a]"
                                                    )}>
                                                        {file.tapeId}
                                                    </span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>

                {/* Right Pane: Restore Wizard */}
                <div className="w-[400px] bg-[#121214] flex flex-col shrink-0 border-l border-[#27272a] shadow-2xl z-10 transition-all">
                    {/* Header with Breadcrumbs */}
                    <div className="p-4 border-b border-[#27272a] bg-[#18181b]/50">
                        <div className="flex gap-1 items-center mb-1">
                            {['Selection', 'Destination', 'Options', 'Confirm'].map((s, i) => (
                                <React.Fragment key={s}>
                                    <span className={cn(
                                        "text-[8px] font-bold uppercase tracking-[0.2em]",
                                        wizardStep === s.toLowerCase() ? "text-primary" : "text-[#3f3f46]"
                                    )}>{s}</span>
                                    {i < 3 && <span className="material-symbols-outlined text-[10px] text-[#27272a]">chevron_right</span>}
                                </React.Fragment>
                            ))}
                        </div>
                        <h2 className="text-[11px] font-bold text-white uppercase tracking-[0.2em]">
                            {wizardStep === 'selection' ? 'Review Cart' : wizardStep === 'destination' ? 'Select Target' : wizardStep === 'options' ? 'Setup Parameters' : 'Final Review'}
                        </h2>
                    </div>

                    {/* Step Content */}
                    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4 custom-scrollbar">
                        {wizardStep === 'selection' && (
                            <div className="flex flex-col gap-2">
                                {selectedFiles.length === 0 ? (
                                    <div className="p-8 text-center text-[#3f3f46] flex flex-col items-center gap-2">
                                        <span className="material-symbols-outlined text-4xl opacity-20">shopping_cart</span>
                                        <span className="text-[10px] font-bold uppercase tracking-widest">No files selected</span>
                                    </div>
                                ) : selectedFiles.map((file) => (
                                    <div key={file.id} className="flex gap-3 p-3 rounded bg-black/30 border border-[#27272a] group hover:border-primary/20 transition-all">
                                        <span className="material-symbols-outlined text-primary text-[18px]">{file.icon}</span>
                                        <div className="flex-1 min-w-0">
                                            <p className="text-[10px] font-mono font-bold text-white truncate">{file.name}</p>
                                            <p className="text-[9px] text-[#71717a] font-mono mt-1">{file.size} • {file.tapeId}</p>
                                        </div>
                                        <button 
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                toggleFile(file);
                                            }} 
                                            className="text-[#3f3f46] hover:text-destructive transition-colors shrink-0"
                                        >
                                            <span className="material-symbols-outlined text-[16px]">close</span>
                                        </button>
                                    </div>
                                ))}
                            </div>
                        )}

                        {wizardStep === 'destination' && (
                            <div className="flex flex-col gap-3">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest mb-1">Predetermined Targets</label>
                                {mounts.map(m => (
                                    <button
                                        key={m.id}
                                        onClick={() => setSelectedDestination(m)}
                                        className={cn(
                                            "flex items-center gap-4 p-4 rounded border transition-all text-left",
                                            selectedDestination?.id === m.id ? "bg-primary/5 border-primary shadow-[0_0_15px_rgba(25,230,100,0.1)]" : "bg-black/40 border-[#27272a] hover:border-[#3f3f46]"
                                        )}
                                    >
                                        <div className={cn("size-10 rounded flex items-center justify-center shrink-0", selectedDestination?.id === m.id ? "bg-primary text-black" : "bg-[#18181b] text-[#71717a]")}>
                                            <span className="material-symbols-outlined">{m.type === 'usb' ? 'usb' : 'location_on'}</span>
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <p className={cn("text-[10px] font-bold uppercase tracking-widest", selectedDestination?.id === m.id ? "text-primary" : "text-white")}>{m.name}</p>
                                            <p className="text-[9px] text-[#71717a] font-mono truncate mt-1">{m.path}</p>
                                        </div>
                                        {selectedDestination?.id === m.id && <span className="material-symbols-outlined text-primary">check_circle</span>}
                                    </button>
                                ))}

                                <div className="mt-4 border-t border-[#27272a] pt-4">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest mb-2 block">Custom Recovery Path</label>
                                    <div className="flex relative rounded-md overflow-hidden border border-[#27272a] bg-black/40">
                                        <input
                                            className="flex-1 block w-full bg-transparent px-3 py-2.5 text-[10px] text-white font-mono outline-none focus:bg-white/[0.02]"
                                            type="text"
                                            value={customDestination}
                                            onChange={(e) => {
                                                setCustomDestination(e.target.value);
                                                setSelectedDestination(null);
                                            }}
                                        />
                                        <div className="flex items-center px-3 bg-[#18181b] text-[#71717a] border-l border-[#27272a]">
                                            <span className="material-symbols-outlined text-[16px]">edit</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )}

                        {wizardStep === 'options' && (
                            <div className="flex flex-col gap-4">
                                {selectedFiles.some((f: any) => f.isEncrypted) && (
                                    <div className="space-y-3 p-4 bg-orange-500/5 border border-orange-500/20 rounded-lg">
                                        <div className="flex items-center gap-2">
                                            <span className="material-symbols-outlined text-orange-400 text-sm">vpn_key</span>
                                            <label className="text-[9px] font-bold text-orange-400 uppercase tracking-widest">Encryption Password</label>
                                        </div>
                                        <input
                                            type="password"
                                            value={decryptionPassword}
                                            onChange={(e) => setDecryptionPassword(e.target.value)}
                                            placeholder="Standard recovery passphrase..."
                                            className="w-full bg-black/60 border border-[#27272a] rounded px-3 py-2.5 text-[10px] font-mono text-white focus:border-orange-500/50 outline-none"
                                        />
                                    </div>
                                )}

                                <div className="flex items-center justify-between p-4 rounded bg-blue-500/5 border border-blue-500/20">
                                    <div className="flex items-center gap-3">
                                        <span className="material-symbols-outlined text-blue-400 text-lg">verified_user</span>
                                        <div>
                                            <p className="text-[10px] font-bold text-blue-400 uppercase tracking-widest">Simulation Mode</p>
                                            <p className="text-[8px] text-blue-400/60 font-medium">Verify without disk writes</p>
                                        </div>
                                    </div>
                                    <Checkbox checked={simulationMode} onChange={setSimulationMode} />
                                </div>
                            </div>
                        )}

                        {wizardStep === 'confirm' && (
                            <div className="flex flex-col gap-6">
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="p-3 bg-[#18181b] rounded border border-[#27272a]">
                                        <p className="text-[8px] text-[#71717a] uppercase tracking-widest mb-1">Total Files</p>
                                        <p className="text-xl font-bold font-mono text-white tracking-tighter">{selectedFiles.length}</p>
                                    </div>
                                    <div className="p-3 bg-[#18181b] rounded border border-[#27272a]">
                                        <p className="text-[8px] text-[#71717a] uppercase tracking-widest mb-1">Aggregate Size</p>
                                        <p className="text-xl font-bold font-mono text-white tracking-tighter">{totalSize}</p>
                                    </div>
                                </div>
                                <div>
                                    <p className="text-[8px] text-[#71717a] uppercase tracking-widest mb-2">Target Destination</p>
                                    <div className="p-3 bg-[#18181b] rounded border border-primary/20 flex gap-3 items-center">
                                        <span className="material-symbols-outlined text-primary text-sm">hard_drive</span>
                                        <span className="text-[10px] font-mono text-white truncate font-bold uppercase tracking-wider">
                                            {selectedDestination ? selectedDestination.path : customDestination}
                                        </span>
                                    </div>
                                </div>
                                <div className="p-3 bg-primary/5 border border-primary/20 rounded-lg flex gap-3 text-primary">
                                    <span className="material-symbols-outlined text-sm mt-0.5">info</span>
                                    <p className="text-[9px] leading-relaxed font-bold uppercase tracking-wider">
                                        System will automatically request tape mounts if required files are currently offline.
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Footer Actions */}
                    <div className="p-6 border-t border-[#27272a] bg-black/20 flex flex-col gap-3">
                        <div className="flex gap-2">
                            {wizardStep !== 'selection' && (
                                <button
                                    onClick={() => setWizardStep(prev => prev === 'confirm' ? 'options' : prev === 'options' ? 'destination' : 'selection')}
                                    className="flex-1 py-3 rounded bg-[#18181b] border border-[#27272a] text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-[0.2em] transition-all"
                                >
                                    Back
                                </button>
                            )}
                            {wizardStep !== 'confirm' ? (
                                <button
                                    onClick={() => setWizardStep(prev => prev === 'selection' ? 'destination' : prev === 'destination' ? 'options' : 'confirm')}
                                    disabled={selectedFiles.length === 0 || (wizardStep === 'destination' && !selectedDestination && !customDestination)}
                                    className="flex-[2] py-3 rounded bg-[#27272a] border border-[#3f3f46] text-white hover:border-primary/50 transition-all text-[10px] font-bold uppercase tracking-[0.2em] disabled:opacity-30"
                                >
                                    Continue
                                </button>
                            ) : (
                                <button
                                    onClick={async () => {
                                        setRestoring(true);
                                        const res = await api.initiateRestore({
                                            files: selectedFiles.map(f => ({ id: f.id, file_path: f.name })),
                                            destination: selectedDestination ? selectedDestination.path : customDestination,
                                            encryption_password: decryptionPassword,
                                            simulation_mode: simulationMode,
                                            confirm: true
                                        });
                                        if (res.success) {
                                            if (totalSlots <= 1) {
                                                const req = res.data?.required_tapes || [];
                                                if (req.length > 0) {
                                                    setTapeLoadRequest({ barcode: req[0], drive: 'DRIVE_A' });
                                                    setShowModal(true);
                                                }
                                            }
                                        }
                                        setRestoring(false);
                                    }}
                                    disabled={restoring}
                                    className="flex-[2] py-3 rounded bg-primary text-black hover:bg-green-400 transition-all text-[10px] font-bold uppercase tracking-[0.2em] shadow-[0_4px_12px_rgba(25,230,100,0.25)] flex items-center justify-center gap-2"
                                >
                                    <span className="material-symbols-outlined text-lg">{restoring ? 'sync' : 'play_arrow'}</span>
                                    {restoring ? 'Starting...' : 'Initiate Restore'}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Verification & Modal Overlays same as before */}
            {verificationResult && (
                <div className="absolute inset-0 z-[110] flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/90 backdrop-blur-md"></div>
                    <div className="relative w-full max-w-2xl bg-[#121214] rounded overflow-hidden shadow-2xl border border-[#27272a]">
                        <div className="p-6 border-b border-[#27272a] flex justify-between items-center">
                            <h3 className="text-[11px] font-bold text-white uppercase tracking-widest">DR Verification Report</h3>
                            <button onClick={() => setVerificationResult(null)} className="text-[#71717a] hover:text-white">
                                <span className="material-symbols-outlined">close</span>
                            </button>
                        </div>
                        <div className="p-6">
                            <pre className="bg-black/60 p-4 rounded border border-[#27272a] text-[10px] text-blue-400 font-mono leading-relaxed whitespace-pre-wrap max-h-[400px] overflow-y-auto custom-scrollbar">
                                {verificationResult.summary_text}
                            </pre>
                            <div className="mt-6 flex justify-end gap-3">
                                <button onClick={() => setVerificationResult(null)} className="px-6 py-2 rounded bg-[#27272a] text-white text-[10px] font-bold uppercase tracking-widest">Dismiss</button>
                                <button onClick={() => window.print()} className="px-6 py-2 rounded bg-primary text-black text-[10px] font-bold uppercase tracking-widest">Print Certificate</button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {showModal && (
                <div className="absolute inset-0 z-[100] flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/90 backdrop-blur-md animate-in fade-in"></div>
                    <div className="relative w-full max-w-md bg-[#121214] rounded border border-[#27272a] animate-in zoom-in-95">
                        <div className="p-8 flex flex-col items-center text-center gap-6">
                            <div className="relative size-20">
                                <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping"></div>
                                <div className="relative rounded-full bg-[#18181b] border-2 border-primary w-full h-full flex items-center justify-center text-primary">
                                    <span className="material-symbols-outlined !text-[40px]">settings_input_component</span>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <h3 className="text-sm font-bold text-white uppercase tracking-widest">Media Insertion Required</h3>
                                <p className="text-[9px] text-[#71717a] font-bold uppercase tracking-[0.15em] leading-relaxed">System is awaiting volume mount</p>
                            </div>
                            <div className="w-full bg-black/60 border border-[#27272a] rounded p-5 flex flex-col items-center gap-3">
                                <span className="text-[8px] text-[#71717a] uppercase tracking-[0.3em] font-bold">{tapeLoadRequest?.drive || 'DRIVE_A'}</span>
                                <div className="font-mono text-3xl font-bold text-primary tracking-[0.25em]">{tapeLoadRequest?.barcode || 'LTO-TAPE'}</div>
                            </div>
                            <button onClick={() => setShowModal(false)} className="w-full py-3 rounded bg-primary text-black text-[10px] font-bold uppercase tracking-[0.2em]">Confirm Tape Loaded</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

