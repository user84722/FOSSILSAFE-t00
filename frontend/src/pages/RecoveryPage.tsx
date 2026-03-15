import { useState, useEffect } from "react"
import { api, Tape } from "@/lib/api"
import { useDemoMode } from "@/lib/demoMode"
import CatalogRebuildWizard from "@/components/recovery/CatalogRebuildWizard"
import EmergencyDumpModal from "@/components/recovery/EmergencyDumpModal"
import ExternalCatalogImportModal from "@/components/recovery/ExternalCatalogImportModal"
import RecoveryChecklist from "@/components/recovery/RecoveryChecklist"

export default function RecoveryPage() {
    const { isDemoMode } = useDemoMode()
    const [tapes, setTapes] = useState<Tape[]>([])
    const [loading, setLoading] = useState(!isDemoMode)
    const [error, setError] = useState<string | null>(null)
    const [showRebuildWizard, setShowRebuildWizard] = useState(false)
    const [showDumpModal, setShowDumpModal] = useState(false)
    const [showImportModal, setShowImportModal] = useState(false)
    const [showSystemRestoreModal, setShowSystemRestoreModal] = useState(false)
    const [showTapeLoadModal, setShowTapeLoadModal] = useState(false)
    const [restoring, setRestoring] = useState(false)
    const selectedBarcode = 'LTO-8-0042' // Local constant for demo/initial

    useEffect(() => {
        const fetchTapes = async () => {
            if (isDemoMode) {
                setTapes([
                    { barcode: 'LTO-8-0042', status: 'available', location_type: 'slot' },
                    { barcode: 'LTO-8-0039', status: 'available', location_type: 'slot' },
                    { barcode: 'LTO-8-0035', status: 'available', location_type: 'slot' }
                ])
                setLoading(false)
                return
            }
            setLoading(true)
            setError(null)
            try {
                const res = await api.getTapes()
                if (res.success && res.data) {
                    setTapes(res.data.tapes)
                } else {
                    setError(res.error || 'Failed to load tapes')
                }
            } catch (err) {
                setError('Connection error. Please check backend status.')
            } finally {
                setLoading(false)
            }
        }
        fetchTapes()
    }, [isDemoMode])

    const handleSystemRestoreConfirm = async () => {
        setShowSystemRestoreModal(false);
        setRestoring(true);

        try {
            const res = await api.initiateRestore({
                files: [], // All files implicit for system restore
                destination: '/',
                restoreType: 'system'
            });

            if (res.success) {
                setShowTapeLoadModal(true);
            } else {
                console.error(res.error || 'System restore failed to initiate');
            }
        } catch (err) {
            console.error('API Error:', err);
        } finally {
            setRestoring(false);
        }
    };

    // ... (keep auth check comment if needed)

    return (
        <div className="flex flex-col gap-8">
            <header className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-bold tracking-tight text-white uppercase antialiased">Disaster Recovery</h1>
                    <div className="flex items-center gap-2 mt-1">
                        <span className="px-1.5 py-0.5 rounded text-[8px] font-mono font-bold bg-destructive/10 text-destructive border border-destructive/20 uppercase tracking-widest">CRITICAL SYSTEM AREA</span>
                    </div>
                </div>
            </header>

            {/* Loading State */}
            {loading && (
                <div className="flex items-center justify-center py-12">
                    <div className="flex flex-col items-center gap-3">
                        <div className="size-8 rounded-full border-2 border-primary/20 border-t-primary animate-spin"></div>
                        <span className="text-[10px] font-bold uppercase tracking-widest text-[#71717a]">Loading Recovery Options...</span>
                    </div>
                </div>
            )}

            {/* Error State */}
            {error && (
                <div className="p-4 rounded bg-destructive/10 border border-destructive/20 flex items-center gap-3">
                    <span className="material-symbols-outlined text-destructive">error</span>
                    <div className="flex-1">
                        <p className="text-sm text-destructive font-bold">{error}</p>
                        <p className="text-[10px] text-destructive/70 mt-1">Recovery operations may be limited until connection is restored.</p>
                    </div>
                    <button
                        onClick={() => window.location.reload()}
                        className="px-3 py-1.5 rounded bg-destructive/20 border border-destructive/30 text-destructive text-[10px] font-bold uppercase tracking-wider hover:bg-destructive/30 transition-all"
                    >
                        Retry
                    </button>
                </div>
            )}

            {/* Recovery Readiness Checklist */}
            <RecoveryChecklist />

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {/* Card 1: Catalog Rebuild */}
                <div className="bg-[#121214] border border-[#27272a] rounded-lg p-6 group hover:border-primary/50 transition-all cursor-pointer" onClick={() => setShowRebuildWizard(true)}>
                    <div className="size-12 rounded-lg bg-primary/10 flex items-center justify-center mb-4 group-hover:bg-primary/20 transition-colors">
                        <span className="material-symbols-outlined text-2xl text-primary">medical_services</span>
                    </div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-2">Rebuild Catalog</h3>
                    <p className="text-xs text-[#71717a] leading-relaxed">
                        Scan tapes to reconstruct the file catalog database. Use this if the primary database is lost or corrupted.
                    </p>
                </div>

                {/* Card 2: External Import */}
                <div
                    className="bg-[#121214] border border-[#27272a] rounded-lg p-6 group hover:border-primary/50 transition-all cursor-pointer"
                    onClick={() => setShowImportModal(true)}
                >
                    <div className="size-12 rounded-lg bg-[#27272a] flex items-center justify-center mb-4 group-hover:bg-primary/10 group-hover:text-primary transition-colors">
                        <span className="material-symbols-outlined text-2xl text-[#71717a] group-hover:text-primary transition-colors">input</span>
                    </div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-2">Import External Catalog</h3>
                    <p className="text-xs text-[#71717a] leading-relaxed">
                        Import catalog metadata from an exported JSON/XML file or a foreign tape set.
                    </p>
                </div>

                {/* Card 3: Emergency Restore */}
                <div
                    className="bg-[#121214] border border-[#27272a] rounded-lg p-6 group hover:border-[#3f3f46] transition-all cursor-pointer border-l-4 border-l-destructive"
                    onClick={() => setShowDumpModal(true)}
                >
                    <div className="size-12 rounded-lg bg-destructive/10 flex items-center justify-center mb-4 group-hover:bg-destructive/20 transition-colors">
                        <span className="material-symbols-outlined text-2xl text-destructive">sos</span>
                    </div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-2">Emergency Dump</h3>
                    <p className="text-xs text-[#71717a] leading-relaxed">
                        Bypass catalog and dump tape contents directly to disk. Use only in extreme data loss scenarios.
                    </p>
                </div>

                {/* Card 4: System Restore */}
                <div
                    className="bg-[#121214] border border-[#27272a] rounded-lg p-6 group hover:border-destructive/50 transition-all cursor-pointer border-l-4 border-l-destructive"
                    onClick={() => setShowSystemRestoreModal(true)}
                >
                    <div className="size-12 rounded-lg bg-destructive/10 flex items-center justify-center mb-4 group-hover:bg-destructive/20 transition-colors">
                        <span className="material-symbols-outlined text-2xl text-destructive">settings_backup_restore</span>
                    </div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider mb-2">Full System Restore</h3>
                    <p className="text-xs text-[#71717a] leading-relaxed">
                        Revert the entire appliance to a previous state. This overwrites the database, settings, and user accounts.
                    </p>
                    <div className="mt-4 pt-4 border-t border-[#27272a] flex items-center gap-2 text-[10px] font-bold text-destructive uppercase tracking-widest">
                        <span className="material-symbols-outlined text-sm">warning</span>
                        Destructive Action
                    </div>
                </div>
            </div>

            <CatalogRebuildWizard
                isOpen={showRebuildWizard}
                onClose={() => setShowRebuildWizard(false)}
                tapes={tapes.map(t => ({ barcode: t.barcode, status: t.status }))}
            />

            <EmergencyDumpModal
                isOpen={showDumpModal}
                onClose={() => setShowDumpModal(false)}
                tapes={tapes.map(t => ({ barcode: t.barcode, status: t.status }))}
            />

            <ExternalCatalogImportModal
                isOpen={showImportModal}
                onClose={() => setShowImportModal(false)}
                tapes={tapes.map(t => ({ barcode: t.barcode, status: t.status }))}
            />

            {/* System Restore Confirmation Modal */}
            {showSystemRestoreModal && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/90 backdrop-blur-md animate-in fade-in duration-300" onClick={() => setShowSystemRestoreModal(false)} />
                    <div className="relative w-full max-w-md bg-[#121214] rounded overflow-hidden shadow-2xl border border-destructive/50 animate-in zoom-in-95 duration-200">
                        <div className="p-8 flex flex-col items-center text-center gap-6">
                            <div className="relative size-20">
                                <div className="absolute inset-0 rounded-full bg-destructive/20 animate-ping"></div>
                                <div className="relative rounded-full bg-[#18181b] border-2 border-destructive w-full h-full flex items-center justify-center text-destructive">
                                    <span className="material-symbols-outlined !text-[40px]">warning</span>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <h3 className="text-xl font-bold text-white uppercase tracking-widest antialiased">System Restore</h3>
                                <div className="p-4 bg-destructive/5 border border-destructive/20 rounded text-left">
                                    <p className="text-[11px] text-[#a1a1aa] font-medium leading-relaxed">
                                        This process will roll back the entire FossilSafe environment to a known good state from tape.
                                        <br /><br />
                                        <strong className="text-white uppercase tracking-wider">What happens:</strong>
                                        <ul className="list-disc list-inside mt-2 space-y-1">
                                            <li>Current database is overwritten</li>
                                            <li>Appliance configuration is reverted</li>
                                            <li>User accounts and 2FA are reset</li>
                                            <li>All current jobs will be aborted</li>
                                        </ul>
                                    </p>
                                </div>
                                <p className="text-[10px] text-destructive font-bold uppercase tracking-widest">This operation is irreversible</p>
                            </div>

                            <div className="w-full flex flex-col gap-3">
                                <button
                                    onClick={handleSystemRestoreConfirm}
                                    disabled={restoring}
                                    className="w-full py-3 rounded bg-destructive hover:bg-red-600 text-white text-[10px] font-bold uppercase tracking-[0.2em] transition-all shadow-[0_0_20px_rgba(239,68,68,0.3)] disabled:opacity-50 flex justify-center items-center gap-2"
                                >
                                    {restoring && <span className="material-symbols-outlined text-sm animate-spin">sync</span>}
                                    {restoring ? 'Initializing...' : 'Proceed with System Restore'}
                                </button>
                                <button onClick={() => setShowSystemRestoreModal(false)} className="text-[9px] font-bold text-[#71717a] hover:text-white uppercase tracking-widest transition-colors py-2">
                                    Cancel
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Tape Load Modal */}
            {showTapeLoadModal && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/95 backdrop-blur-xl animate-in fade-in duration-300" />
                    <div className="relative w-full max-w-md bg-[#121214] rounded overflow-hidden shadow-2xl border border-[#27272a] animate-in zoom-in-95 duration-200">
                        <div className="p-8 flex flex-col items-center text-center gap-6">
                            <div className="relative size-20">
                                <div className="absolute inset-0 rounded-full bg-primary/20 animate-ping"></div>
                                <div className="relative rounded-full bg-[#18181b] border-2 border-primary w-full h-full flex items-center justify-center text-primary shadow-[0_0_20px_rgba(25,230,100,0.2)]">
                                    <span className="material-symbols-outlined !text-[40px]">settings_input_component</span>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <h3 className="text-xl font-bold text-white uppercase tracking-widest antialiased">Tape Load Required</h3>
                                <p className="text-[10px] text-[#71717a] font-bold uppercase tracking-wider max-w-xs mx-auto leading-relaxed">
                                    The system restore image is located on an offline volume. Physical media insertion is required.
                                </p>
                            </div>

                            <div className="w-full bg-black/60 border border-[#27272a] rounded p-5 flex flex-col items-center gap-3">
                                <span className="text-[9px] text-[#71717a] uppercase tracking-[0.3em] font-bold">Recommended Volume</span>
                                <div className="font-mono text-3xl font-bold text-primary tracking-[0.25em]">{selectedBarcode}</div>
                                <div className="flex items-center gap-2 mt-2 px-3 py-1 rounded-full bg-[#18181b] border border-[#27272a]">
                                    <div className="size-1.5 rounded-full bg-amber-500 animate-pulse"></div>
                                    <span className="text-[8px] text-[#71717a] font-bold uppercase tracking-widest">Awaiting Media Insertion</span>
                                </div>
                            </div>

                            <div className="w-full flex flex-col gap-3">
                                <button
                                    className="w-full py-3 rounded bg-primary hover:bg-green-400 text-black text-[10px] font-bold uppercase tracking-[0.2em] transition-all"
                                    onClick={() => setShowTapeLoadModal(false)}
                                >
                                    Confirm Tape Loaded
                                </button>
                                <button onClick={() => setShowTapeLoadModal(false)} className="text-[9px] font-bold text-[#71717a] hover:text-white uppercase tracking-widest transition-colors py-2">
                                    Abort Operation
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
