import { useState, useEffect } from "react"
import { api } from "@/lib/api"
import { useDemoMode } from "@/lib/demoMode"

interface EmergencyDumpModalProps {
    isOpen: boolean
    onClose: () => void
    tapes: Array<{ barcode: string; status: string }>
}

export default function EmergencyDumpModal({ isOpen, onClose, tapes }: EmergencyDumpModalProps) {
    const { isDemoMode } = useDemoMode()
    const [step, setStep] = useState<'select' | 'confirm' | 'progress' | 'complete'>('select')
    const [selectedBarcode, setSelectedBarcode] = useState('')
    const [destinationPath, setDestinationPath] = useState('/tmp/recovery_dump')
    const [progress, setProgress] = useState(0)
    const [logs, setLogs] = useState<string[]>([])
    const [loading, setLoading] = useState(false); // Assuming setLoading and setError are needed for handleDump
    const [error, setError] = useState<string | null>(null); // Assuming setLoading and setError are needed for handleDump

    // Reset state on open
    useEffect(() => {
        if (isOpen) {
            setStep('select')
            setProgress(0)
            setLogs([])
            setSelectedBarcode('')
        }
    }, [isOpen])

    const handleDump = async () => {
        if (!selectedBarcode || !destinationPath) return; // Changed 'barcode' to 'selectedBarcode' to match component state

        setLoading(true);
        try {
            const res = await api.startEmergencyDump(selectedBarcode, destinationPath); // Changed 'barcode' to 'selectedBarcode'
            if (res.success) {
                onClose();
                // Optionally show global toast or notification
            } else {
                setError(res.error || 'Failed to start dump');
            }
        } catch (err: any) {
            setError(err.message || 'An error occurred');
        } finally {
            setLoading(false);
        }
    };

    const handleStartDump = async () => {
        setStep('progress')
        setLogs(prev => [...prev, `[INIT] Starting emergency dump for tape ${selectedBarcode}...`])

        if (isDemoMode) {
            // Simulation
            const steps = [
                "Mounting tape drive...",
                "Rewinding tape...",
                "Bypassing LTFS catalog...",
                "Block read started (raw mode)...",
                "Extracting file: /contracts/2023/may_final.pdf",
                "Extracting file: /videos/render_v2.mov",
                "Extracting file: /db_dumps/april.sql",
                "Verifying block checksums...",
                "Dump complete."
            ]

            for (let i = 0; i < steps.length; i++) {
                await new Promise(r => setTimeout(r, 800))
                setLogs(prev => [...prev, `[INFO] ${steps[i]}`])
                setProgress(((i + 1) / steps.length) * 100)
            }

            await new Promise(r => setTimeout(r, 500))
            setStep('complete')
        } else {
            // Real API Call
            try {
                const res = await api.startEmergencyDump(selectedBarcode, destinationPath)
                if (res.success) {
                    setLogs(prev => [...prev, `[SUCCESS] Dump job initiated. Job ID: ${res.data?.job_id}`]);
                    setLogs(prev => [...prev, `[INFO] ${res.data?.message}`]);
                    setStep('complete'); // Or create a polling mechanism if we want to track it live here
                } else {
                    throw new Error(res.error || 'Failed to start dump');
                }
            } catch (e) {
                setLogs(prev => [...prev, `[ERROR] Failed to start dump: ${e instanceof Error ? e.message : String(e)}`])
                // Stay on progress step but show error
            }
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
            <div className="relative w-full max-w-lg bg-[#121214] border border-destructive/50 rounded-lg shadow-2xl overflow-hidden">

                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#27272a] bg-destructive/10">
                    <div className="flex items-center gap-3">
                        <span className="material-symbols-outlined text-destructive">sos</span>
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Emergency Tape Dump</h2>
                        </div>
                    </div>
                </div>

                <div className="p-6">
                    {step === 'select' && (
                        <div className="space-y-4">
                            <div className="p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs leading-relaxed">
                                <strong className="uppercase">Warning:</strong> This tool performs a raw sequential read of the tape, ignoring the file catalog. File metadata (permissions, creation dates) may be lost. Use only if "Rebuild Catalog" fails.
                            </div>

                            <div className="space-y-2">
                                <label className="text-[10px] font-bold text-[#71717a] uppercase tracking-widest">Select Source Tape</label>
                                <select
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-destructive/50 outline-none"
                                    value={selectedBarcode}
                                    onChange={e => setSelectedBarcode(e.target.value)}
                                >
                                    <option value="">-- Select Tape --</option>
                                    {tapes.length > 0 ? tapes.map(t => (
                                        <option key={t.barcode} value={t.barcode}>{t.barcode} ({t.status})</option>
                                    )) : (
                                        <option value="DEMO-TAPE-01">DEMO-TAPE-01 (Offline)</option>
                                    )}
                                </select>
                            </div>

                            <div className="space-y-2">
                                <label className="text-[10px] font-bold text-[#71717a] uppercase tracking-widest">Destination Path</label>
                                <input
                                    type="text"
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-destructive/50 outline-none font-mono"
                                    value={destinationPath}
                                    onChange={e => setDestinationPath(e.target.value)}
                                />
                            </div>

                            <div className="flex justify-end gap-3 mt-6">
                                <button
                                    onClick={onClose}
                                    className="px-4 py-2 rounded text-[#a1a1aa] hover:text-white hover:bg-[#27272a] transition-colors text-xs font-bold uppercase tracking-wider"
                                    disabled={loading}
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleDump}
                                    disabled={!selectedBarcode || !destinationPath || loading}
                                    className="px-4 py-2 rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors text-xs font-bold uppercase tracking-wider flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {loading ? (
                                        <>
                                            <span className="material-symbols-outlined text-sm animate-spin">refresh</span>
                                            Starting...
                                        </>
                                    ) : (
                                        <>
                                            <span className="material-symbols-outlined text-sm">warning</span>
                                            Start Emergency Dump
                                        </>
                                    )}
                                </button>
                            </div>
                            {error && (
                                <div className="mt-4 p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs font-mono">
                                    {error}
                                </div>
                            )}    </div>
                    )}

                    {step === 'confirm' && (
                        <div className="space-y-6 text-center">
                            <span className="material-symbols-outlined text-4xl text-destructive animate-pulse">warning</span>
                            <div className="space-y-2">
                                <h3 className="text-white font-bold uppercase tracking-wider">Confirm Emergency Dump</h3>
                                <p className="text-xs text-[#71717a]">
                                    You are about to dump contents of <strong>{selectedBarcode}</strong> to <strong>{destinationPath}</strong>.
                                    <br />This operation cannot be paused.
                                </p>
                            </div>
                            <div className="pt-4 flex justify-center gap-2">
                                <button onClick={() => setStep('select')} className="px-4 py-2 text-xs font-bold text-[#71717a] hover:text-white uppercase tracking-wider">Back</button>
                                <button
                                    onClick={handleStartDump}
                                    className="px-4 py-2 bg-destructive hover:bg-red-600 text-white text-xs font-bold uppercase tracking-wider rounded shadow-[0_0_15px_rgba(239,68,68,0.4)]"
                                >
                                    Start Dump
                                </button>
                            </div>
                        </div>
                    )}

                    {(step === 'progress' || step === 'complete') && (
                        <div className="space-y-4">
                            <div className="h-1.5 w-full bg-[#27272a] rounded-full overflow-hidden">
                                <div className="h-full bg-destructive transition-all duration-300" style={{ width: `${progress}%` }}></div>
                            </div>

                            <div className="h-48 bg-black rounded border border-[#27272a] p-3 font-mono text-[10px] overflow-y-auto custom-scrollbar">
                                {logs.map((log, i) => (
                                    <div key={i} className="text-white/80 border-b border-white/5 pb-0.5 mb-0.5">{log}</div>
                                ))}
                                {step === 'progress' && (
                                    <div className="animate-pulse text-destructive">_</div>
                                )}
                            </div>

                            {step === 'complete' && (
                                <div className="flex justify-center">
                                    <button
                                        onClick={onClose}
                                        className="px-6 py-2 bg-[#27272a] hover:bg-white/10 text-white text-xs font-bold uppercase tracking-wider rounded"
                                    >
                                        Close
                                    </button>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
