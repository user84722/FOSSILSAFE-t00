import { useState } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"
import { useDemoMode } from "@/lib/demoMode"

type WizardStep = 'source' | 'scan' | 'review' | 'rebuild' | 'success'

interface CatalogRebuildWizardProps {
    isOpen: boolean
    onClose: () => void
    tapes?: Array<{ barcode: string; alias?: string; status: string }>
}

export default function CatalogRebuildWizard({ isOpen, onClose, tapes = [] }: CatalogRebuildWizardProps) {
    const { isDemoMode } = useDemoMode()
    const [step, setStep] = useState<WizardStep>('source')
    const [selectedTape, setSelectedTape] = useState('')
    const [scanMode, setScanMode] = useState<'single' | 'all'>('single')
    const [isScanning, setIsScanning] = useState(false)
    const [scanProgress, setScanProgress] = useState(0)
    const [scanResult, setScanResult] = useState<any>(null)
    const [isRebuilding, setIsRebuilding] = useState(false)
    const [error, setError] = useState('')

    const steps: WizardStep[] = ['source', 'scan', 'review', 'rebuild', 'success']
    const currentStepIndex = steps.indexOf(step)
    const progress = ((currentStepIndex + 1) / steps.length) * 100

    const resetWizard = () => {
        setStep('source')
        setSelectedTape('')
        setScanMode('single')
        setIsScanning(false)
        setScanProgress(0)
        setScanResult(null)
        setIsRebuilding(false)
        setError('')
    }

    const handleClose = () => {
        if (isScanning || isRebuilding) return // Prevent closing during active ops
        resetWizard()
        onClose()
    }

    const startScan = async () => {
        if (scanMode === 'all') {
            startRebuild()
            return
        }

        if (!selectedTape) return
        setStep('scan')
        setIsScanning(true)
        setScanProgress(0)
        setError('')

        if (isDemoMode) {
            // Simulate scan
            const interval = setInterval(() => {
                setScanProgress(prev => {
                    if (prev >= 100) {
                        clearInterval(interval)
                        setIsScanning(false)
                        setScanResult({
                            snapshots: [
                                { id: 'snap_01', date: '2023-10-15', size: '1.2 TB', files: 15420 },
                                { id: 'snap_02', date: '2023-11-20', size: '0.8 TB', files: 8900 }
                            ],
                            tape_label: 'ARCHIVE_2023'
                        })
                        setStep('review')
                        return 100
                    }
                    return prev + 5
                })
            }, 100)
        } else {
            try {
                const res = await api.scanTapeForCatalog(selectedTape)
                if (res.success && res.data) {
                    setScanResult(res.data.catalog)
                    setStep('review')
                } else {
                    throw new Error(res.error || 'Scan failed')
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Scan failed')
                setStep('source')
            } finally {
                setIsScanning(false)
            }
        }
    }

    const startRebuild = async () => {
        setStep('rebuild')
        setIsRebuilding(true)
        setError('')

        if (isDemoMode) {
            // Simulate rebuild
            setTimeout(() => {
                setIsRebuilding(false)
                setStep('success')
            }, 2000)
        } else {
            try {
                const params = scanMode === 'all'
                    ? { scan_all: true }
                    : { tape_barcodes: [selectedTape] }

                const res = await api.rebuildCatalog(params)
                if (res.success) {
                    setStep('success')
                } else {
                    throw new Error(res.error || 'Rebuild failed')
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Rebuild failed')
                setStep(scanMode === 'all' ? 'source' : 'review')
            } finally {
                setIsRebuilding(false)
            }
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={handleClose} />

            <div className="relative w-full max-w-2xl mx-4 bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden">
                {/* Progress Bar */}
                <div className="h-1 bg-[#27272a]">
                    <div className="h-full bg-primary transition-all duration-300" style={{ width: `${progress}%` }} />
                </div>

                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#27272a]">
                    <div className="flex items-center gap-3">
                        <span className="material-symbols-outlined text-primary">medical_services</span>
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Catalog Rebuild Wizard</h2>
                            <p className="text-[10px] text-[#71717a] font-mono uppercase">Step {currentStepIndex + 1} of {steps.length}</p>
                        </div>
                    </div>
                    <button onClick={handleClose} className="text-[#71717a] hover:text-white transition-colors">
                        <span className="material-symbols-outlined">close</span>
                    </button>
                </div>

                {/* Content */}
                <div className="p-6 min-h-[320px]">
                    {error && (
                        <div className="mb-4 p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs">
                            {error}
                        </div>
                    )}

                    {/* Step 1: Source */}
                    {step === 'source' && (
                        <div className="space-y-4">
                            <div className="text-center mb-6">
                                <h3 className="text-lg font-semibold text-white">Select Source Tape</h3>
                                <p className="text-xs text-[#71717a] mt-1">Select the tape containing the catalog metadata or backup data</p>
                            </div>
                            <div className="flex gap-4 mb-6">
                                <button
                                    onClick={() => setScanMode('single')}
                                    className={cn(
                                        "flex-1 p-4 rounded border transition-all text-left",
                                        scanMode === 'single' ? "bg-primary/5 border-primary shadow-[0_0_15px_rgba(25,230,100,0.1)]" : "bg-black/20 border-[#27272a] hover:border-[#3f3f46]"
                                    )}
                                >
                                    <div className="flex items-center gap-2 mb-1">
                                        <div className={cn("size-2 rounded-full", scanMode === 'single' ? "bg-primary" : "bg-[#3f3f46]")} />
                                        <span className="text-xs font-bold text-white uppercase tracking-wider">Single Tape</span>
                                    </div>
                                    <p className="text-[10px] text-[#71717a]">Fast scan of a specific, known tape.</p>
                                </button>
                                <button
                                    onClick={() => setScanMode('all')}
                                    className={cn(
                                        "flex-1 p-4 rounded border transition-all text-left",
                                        scanMode === 'all' ? "bg-primary/5 border-primary shadow-[0_0_15px_rgba(25,230,100,0.1)]" : "bg-black/20 border-[#27272a] hover:border-[#3f3f46]"
                                    )}
                                >
                                    <div className="flex items-center gap-2 mb-1">
                                        <div className={cn("size-2 rounded-full", scanMode === 'all' ? "bg-primary" : "bg-[#3f3f46]")} />
                                        <span className="text-xs font-bold text-white uppercase tracking-wider">Full Library</span>
                                    </div>
                                    <p className="text-[10px] text-[#71717a]">Iterative scan of all tapes in inventory.</p>
                                </button>
                            </div>

                            {scanMode === 'single' && (
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Tape Barcode</label>
                                    <select
                                        value={selectedTape}
                                        onChange={(e) => setSelectedTape(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                    >
                                        <option value="">Select a tape...</option>
                                        {tapes.map(t => (
                                            <option key={t.barcode} value={t.barcode}>{t.barcode} {t.alias ? `(${t.alias})` : ''}</option>
                                        ))}
                                        {isDemoMode && tapes.length === 0 && (
                                            <option value="TAPE_RECOVERY_01">TAPE_RECOVERY_01 (Emergency Backup)</option>
                                        )}
                                    </select>
                                </div>
                            )}

                            {scanMode === 'all' && (
                                <div className="p-4 bg-primary/5 border border-primary/20 rounded">
                                    <div className="text-xs font-bold text-white uppercase mb-2">Library Scan Summary</div>
                                    <div className="flex justify-between items-center text-[10px] text-[#71717a]">
                                        <span>Total Tapes Found:</span>
                                        <span className="text-white font-mono">{tapes.length || (isDemoMode ? 8 : 0)}</span>
                                    </div>
                                </div>
                            )}
                            <div className="p-4 bg-warning/5 border border-warning/20 rounded text-warning text-xs mt-4">
                                <div className="flex items-center gap-2 font-bold mb-1 uppercase tracking-wider text-[10px]">
                                    <span className="material-symbols-outlined text-sm">warning</span>
                                    Warning
                                </div>
                                Scanning a tape can take several hours depending on the tape generation and amount of data.
                                The drive will be locked during this process.
                            </div>
                        </div>
                    )}

                    {/* Step 2: Scan */}
                    {step === 'scan' && (
                        <div className="flex flex-col items-center justify-center h-full py-12 space-y-6">
                            <div className="relative size-24 flex items-center justify-center">
                                <svg className="size-full -rotate-90" viewBox="0 0 36 36">
                                    <path className="text-[#27272a]" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="currentColor" strokeWidth="2" />
                                    <path className="text-primary transition-all duration-300 ease-linear" strokeDasharray={`${scanProgress}, 100`} d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="currentColor" strokeWidth="2" />
                                </svg>
                                <span className="absolute text-xl font-mono font-bold text-white">{scanProgress}%</span>
                            </div>
                            <div className="text-center">
                                <h3 className="text-lg font-bold text-white animate-pulse">Scanning Tape...</h3>
                                <p className="text-xs text-[#71717a] font-mono mt-2">Reading headers and metadata blocks</p>
                            </div>
                        </div>
                    )}

                    {/* Step 3: Review */}
                    {step === 'review' && scanResult && (
                        <div className="space-y-4">
                            <div className="text-center mb-6">
                                <h3 className="text-lg font-semibold text-white">Scan Complete</h3>
                                <p className="text-xs text-[#71717a] mt-1">Found the following recoverble data</p>
                            </div>
                            <div className="bg-[#09090b] border border-[#27272a] rounded-lg p-4">
                                <div className="flex justify-between items-center mb-4 border-b border-[#27272a] pb-2">
                                    <span className="text-[10px] text-[#71717a] uppercase tracking-wider">Tape Label</span>
                                    <span className="text-sm font-mono text-white font-bold">{scanResult.tape_label || selectedTape}</span>
                                </div>
                                <div className="space-y-2">
                                    {scanResult.snapshots?.map((snap: any, i: number) => (
                                        <div key={i} className="flex justify-between items-center bg-[#18181b] p-3 rounded border border-[#27272a]">
                                            <div>
                                                <div className="text-xs font-bold text-white uppercase">Snapshot {snap.id}</div>
                                                <div className="text-[10px] text-[#71717a]">{snap.date}</div>
                                            </div>
                                            <div className="text-right">
                                                <div className="text-xs font-mono text-primary">{snap.size}</div>
                                                <div className="text-[10px] text-[#71717a]">{snap.files} files</div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Step 4: Rebuild */}
                    {step === 'rebuild' && (
                        <div className="flex flex-col items-center justify-center h-full py-12 space-y-6">
                            <span className="material-symbols-outlined text-5xl text-primary animate-spin">sync</span>
                            <div className="text-center">
                                <h3 className="text-lg font-bold text-white">Rebuilding Catalog...</h3>
                                <p className="text-xs text-[#71717a] font-mono mt-2">Importing metadata into database</p>
                            </div>
                        </div>
                    )}

                    {/* Step 5: Success */}
                    {step === 'success' && (
                        <div className="flex flex-col items-center justify-center h-full py-12 space-y-6">
                            <div className="size-20 rounded-full bg-primary/10 flex items-center justify-center border border-primary/20">
                                <span className="material-symbols-outlined text-5xl text-primary">check_circle</span>
                            </div>
                            <div className="text-center">
                                <h3 className="text-xl font-bold text-white">Rebuild Complete</h3>
                                <p className="text-sm text-[#71717a] mt-2">The catalog has been successfully updated.</p>
                            </div>
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="flex items-center justify-between px-6 py-4 border-t border-[#27272a] bg-[#09090b]">
                    {step !== 'scan' && step !== 'rebuild' && step !== 'success' && (
                        <button onClick={handleClose} className="px-4 py-2 text-[#71717a] hover:text-white text-xs font-bold uppercase tracking-wider transition-colors">
                            Cancel
                        </button>
                    )}
                    <div className="flex gap-2 ml-auto">
                        {step === 'source' && (
                            <button
                                onClick={startScan}
                                disabled={scanMode === 'single' && !selectedTape}
                                className={cn(
                                    "px-6 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase tracking-wider rounded transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]",
                                    (scanMode === 'single' && !selectedTape) && "opacity-50 cursor-not-allowed"
                                )}
                            >
                                {scanMode === 'all' ? 'Start Library Scan' : 'Start Scan'}
                            </button>
                        )}
                        {step === 'review' && (
                            <button
                                onClick={startRebuild}
                                className="px-6 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase tracking-wider rounded transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]"
                            >
                                Import to Catalog
                            </button>
                        )}
                        {step === 'success' && (
                            <button
                                onClick={handleClose}
                                className="px-6 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase tracking-wider rounded transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]"
                            >
                                Close Wizard
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    )
}
