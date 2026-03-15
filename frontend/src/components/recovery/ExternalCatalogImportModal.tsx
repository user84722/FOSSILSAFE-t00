import { useState, useEffect } from "react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useDemoMode } from "@/lib/demoMode"

interface ExternalCatalogImportModalProps {
    isOpen: boolean
    onClose: () => void
    tapes?: Array<{ barcode: string; status: string }>
}

export default function ExternalCatalogImportModal({ isOpen, onClose, tapes = [] }: ExternalCatalogImportModalProps) {
    const { isDemoMode } = useDemoMode()
    const [step, setStep] = useState<'upload' | 'scan' | 'success'>('upload')
    const [progress, setProgress] = useState(0)
    const [selectedTape, setSelectedTape] = useState('')
    const [importType, setImportType] = useState<'file' | 'tape'>('file')
    const [error, setError] = useState('')

    useEffect(() => {
        if (isOpen) {
            setStep('upload')
            setProgress(0)
        }
    }, [isOpen])

    const handleImport = async () => {
        if (importType === 'tape' && !selectedTape) return

        setStep('scan')
        setProgress(0)
        setError('')

        if (isDemoMode) {
            // Simulate import process
            const interval = setInterval(() => {
                setProgress(prev => {
                    if (prev >= 100) {
                        clearInterval(interval)
                        setStep('success')
                        return 100
                    }
                    return prev + 10
                })
            }, 300)
        } else {
            try {
                if (importType === 'file') {
                    // Logic for file upload import could go here if supported by backend
                    // For now, we'll focus on the tape import which is the primary DR path
                    throw new Error("File upload import not implemented yet. Please use tape scan.")
                }

                const res = await api.importCatalog(selectedTape)
                if (res.success) {
                    setStep('success')
                } else {
                    throw new Error(res.error || 'Import failed')
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Import failed')
                setStep('upload')
            }
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
            <div className="relative w-full max-w-lg bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden">
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#27272a]">
                    <div className="flex items-center gap-3">
                        <span className="material-symbols-outlined text-primary">input</span>
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Import External Catalog</h2>
                        </div>
                    </div>
                </div>

                <div className="p-6">
                    {step === 'upload' && (
                        <div className="space-y-4">
                            {error && (
                                <div className="p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs">
                                    {error}
                                </div>
                            )}

                            <div className="flex gap-4">
                                <button
                                    onClick={() => setImportType('file')}
                                    className={cn(
                                        "flex-1 p-4 rounded border transition-all text-left",
                                        importType === 'file' ? "bg-primary/5 border-primary" : "bg-black/20 border-[#27272a]"
                                    )}
                                >
                                    <span className="text-xs font-bold text-white uppercase block mb-1">Catalog File</span>
                                    <p className="text-[10px] text-[#71717a]">Upload a JSON/XML export.</p>
                                </button>
                                <button
                                    onClick={() => setImportType('tape')}
                                    className={cn(
                                        "flex-1 p-4 rounded border transition-all text-left",
                                        importType === 'tape' ? "bg-primary/5 border-primary" : "bg-black/20 border-[#27272a]"
                                    )}
                                >
                                    <span className="text-xs font-bold text-white uppercase block mb-1">Recovery Tape</span>
                                    <p className="text-[10px] text-[#71717a]">Scan a foreign FossilSafe tape.</p>
                                </button>
                            </div>

                            {importType === 'file' ? (
                                <div className="border-2 border-dashed border-[#27272a] rounded-lg p-8 flex flex-col items-center justify-center hover:border-primary/50 transition-colors cursor-pointer bg-black/20">
                                    <span className="material-symbols-outlined text-4xl text-[#71717a] mb-2">upload_file</span>
                                    <p className="text-sm font-bold text-white">Drop Catalog JSON/XML Here</p>
                                    <p className="text-xs text-[#71717a] mt-1">or click to browse</p>
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    <label className="text-[AR9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Recovery Tape</label>
                                    <select
                                        value={selectedTape}
                                        onChange={(e) => setSelectedTape(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                    >
                                        <option value="">Select a tape...</option>
                                        {tapes.map(t => (
                                            <option key={t.barcode} value={t.barcode}>{t.barcode}</option>
                                        ))}
                                        {isDemoMode && <option value="EXT_RECOVERY_01">EXT_RECOVERY_01</option>}
                                    </select>
                                </div>
                            )}

                            <div className="flex justify-end gap-2 pt-4">
                                <button onClick={onClose} className="px-4 py-2 text-xs font-bold text-[#71717a] hover:text-white uppercase tracking-wider">Cancel</button>
                                <button
                                    onClick={handleImport}
                                    disabled={importType === 'tape' && !selectedTape}
                                    className={cn(
                                        "px-6 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase tracking-wider rounded transition-all",
                                        (importType === 'tape' && !selectedTape) && "opacity-50 cursor-not-allowed"
                                    )}
                                >
                                    {isDemoMode ? 'Start Import (Demo)' : 'Start Import'}
                                </button>
                            </div>
                        </div>
                    )}

                    {step === 'scan' && (
                        <div className="space-y-4 text-center py-8">
                            <span className="material-symbols-outlined text-4xl text-primary animate-spin">sync</span>
                            <div>
                                <h3 className="text-white font-bold uppercase tracking-wider">Importing Catalog...</h3>
                                <div className="h-1 bg-[#27272a] rounded-full mt-4 overflow-hidden">
                                    <div className="h-full bg-primary transition-all duration-300" style={{ width: `${progress}%` }}></div>
                                </div>
                            </div>
                        </div>
                    )}

                    {step === 'success' && (
                        <div className="space-y-4 text-center py-8">
                            <span className="material-symbols-outlined text-5xl text-primary">check_circle</span>
                            <div>
                                <h3 className="text-white font-bold uppercase tracking-wider">Import Complete</h3>
                                <p className="text-xs text-[#71717a] mt-2">External catalog merged successfully.</p>
                            </div>
                            <button
                                onClick={onClose}
                                className="mt-4 px-6 py-2 bg-[#27272a] hover:bg-white/10 text-white text-xs font-bold uppercase tracking-wider rounded"
                            >
                                Close
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
