import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"
import { AlertTriangle, CheckCircle2, Info, Loader2, Play, Save, Settings } from "lucide-react"

interface CalibrationWizardProps {
    isOpen: boolean
    onClose: () => void
}

type WizardStep = 'welcome' | 'prepare' | 'identify' | 'review' | 'success'

export default function CalibrationWizard({ isOpen, onClose }: CalibrationWizardProps) {
    const [step, setStep] = useState<WizardStep>('welcome')
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState('')

    const [potentialPaths, setPotentialPaths] = useState<string[]>([])
    const [physicalDriveCount, setPhysicalDriveCount] = useState(0)
    const [inventory, setInventory] = useState<any[]>([])
    const [selectedBarcode, setSelectedBarcode] = useState('')

    const [mapping, setMapping] = useState<Record<string, string>>({})
    const [currentIdentifyingIndex, setCurrentIdentifyingIndex] = useState(0)
    const [isProcessing, setIsProcessing] = useState(false)

    useEffect(() => {
        if (isOpen) {
            fetchInitialInfo()
        }
    }, [isOpen])

    const fetchInitialInfo = async () => {
        setIsLoading(true)
        setError('')
        try {
            const res = await api.getCalibrationInfo()
            if (res.success && res.data) {
                setPotentialPaths(res.data.potential_logical_paths)
                setPhysicalDriveCount(res.data.physical_drive_count)
                setInventory(res.data.inventory || [])
                setMapping(res.data.current_mapping || {})

                // Pick first available tape with barcode
                const firstTape = res.data.inventory.find(t => t.barcode && t.status === 'available')
                if (firstTape) {
                    setSelectedBarcode(firstTape.barcode)
                }
            }
        } catch (err: any) {
            setError(err.message || 'Failed to fetch calibration info')
        } finally {
            setIsLoading(false)
        }
    }

    const handleIdentify = async () => {
        if (!selectedBarcode) {
            setError('Please select a tape for calibration')
            return
        }

        setIsProcessing(true)
        setError('')
        try {
            const res = await api.identifyCalibrationDrive(currentIdentifyingIndex, selectedBarcode)
            if (res.success && res.data) {
                setMapping(prev => ({
                    ...prev,
                    [currentIdentifyingIndex]: res.data!.logical_path
                }))

                if (currentIdentifyingIndex + 1 < physicalDriveCount) {
                    setCurrentIdentifyingIndex(prev => prev + 1)
                } else {
                    setStep('review')
                }
            }
        } catch (err: any) {
            setError(err.message || `Failed to identify drive ${currentIdentifyingIndex}`)
        } finally {
            setIsProcessing(false)
        }
    }

    const handleSave = async () => {
        setIsLoading(true)
        setError('')
        try {
            const res = await api.saveCalibrationMapping(mapping)
            if (res.success) {
                setStep('success')
            } else {
                throw new Error(res.error || 'Failed to save calibration')
            }
        } catch (err: any) {
            setError(err.message || 'Failed to save mapping')
        } finally {
            setIsLoading(false)
        }
    }

    const handleClose = () => {
        setStep('welcome')
        setError('')
        setMapping({})
        setCurrentIdentifyingIndex(0)
        onClose()
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-[60] flex items-center justify-center">
            {/* Backdrop */}
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={handleClose} />

            {/* Modal */}
            <div className="relative w-full max-w-xl mx-4 bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#27272a] bg-[#18181b]">
                    <div className="flex items-center gap-3">
                        <Settings className="size-5 text-primary animate-spin-slow" />
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Drive Calibration Wizard</h2>
                            <p className="text-[10px] text-[#71717a] font-mono uppercase">Physical-to-Logical Mapping</p>
                        </div>
                    </div>
                    <button onClick={handleClose} className="text-[#71717a] hover:text-white transition-colors">
                        <span className="material-symbols-outlined">close</span>
                    </button>
                </div>

                {/* Content */}
                <div className="p-8 min-h-[350px] flex flex-col">
                    {error && (
                        <div className="mb-6 p-4 rounded bg-destructive/10 border border-destructive/20 flex gap-3 animate-in fade-in slide-in-from-top-2">
                            <AlertTriangle className="size-5 text-destructive shrink-0" />
                            <div className="text-xs text-destructive font-medium">{error}</div>
                        </div>
                    )}

                    {/* Step: Welcome */}
                    {step === 'welcome' && (
                        <div className="flex-1 space-y-6 text-center py-4">
                            <div className="mx-auto size-16 bg-primary/10 rounded-full flex items-center justify-center border border-primary/20">
                                <Settings className="size-8 text-primary" />
                            </div>
                            <div className="space-y-2">
                                <h3 className="text-lg font-bold text-white">Why Calibrate?</h3>
                                <p className="text-xs text-[#71717a] leading-relaxed max-w-sm mx-auto">
                                    Sometimes the operating system assigns tape drives (e.g. <code>/dev/nst0</code>) in a different
                                    order than the physical library slots. This wizard ensures FossilSafe talks to the correct drive hardware.
                                </p>
                            </div>
                            <div className="p-4 bg-primary/5 border border-primary/10 rounded-lg text-left">
                                <div className="flex gap-2 items-center mb-2">
                                    <Info className="size-3.5 text-primary" />
                                    <span className="text-[10px] font-bold text-primary uppercase tracking-widest">How it works</span>
                                </div>
                                <ul className="text-[10px] space-y-2 text-[#a1a1aa] font-medium italic">
                                    <li>1. You select a tape that is currently in the library.</li>
                                    <li>2. FossilSafe moves that tape into each physical drive.</li>
                                    <li>3. It scans all logical paths to find where the tape appeared.</li>
                                </ul>
                            </div>
                        </div>
                    )}

                    {/* Step: Prepare */}
                    {step === 'prepare' && (
                        <div className="flex-1 space-y-6">
                            <div className="bg-[#09090b] border border-[#27272a] rounded-lg p-4 space-y-4">
                                <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-widest text-[#71717a]">
                                    <span>Hardware Detected</span>
                                    <span className="text-white">{physicalDriveCount} Physical Drives</span>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest block px-1">Calibration Target (Tape)</label>
                                    <select
                                        value={selectedBarcode}
                                        onChange={(e) => setSelectedBarcode(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none transition-all"
                                    >
                                        <option value="">Select a tape...</option>
                                        {inventory.filter(t => t.barcode && t.status === 'available').map(t => (
                                            <option key={t.barcode} value={t.barcode}>{t.barcode} (Slot {t.slot})</option>
                                        ))}
                                    </select>
                                    <p className="text-[9px] text-[#71717a] italic">Choose a tape that is currently available in a storage slot.</p>
                                </div>
                            </div>

                            <div className="space-y-2">
                                <div className="text-[10px] font-bold text-[#71717a] uppercase tracking-widest px-1">Detected Logical Paths</div>
                                <div className="grid grid-cols-2 gap-2">
                                    {potentialPaths.map(path => (
                                        <div key={path} className="px-3 py-2 bg-black/40 border border-[#27272a] rounded font-mono text-[10px] text-[#a1a1aa]">
                                            {path}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Step: Identify */}
                    {step === 'identify' && (
                        <div className="flex-1 flex flex-col items-center justify-center space-y-8 text-center">
                            <div className="space-y-4">
                                <div className="text-sm font-bold text-white uppercase tracking-widest">
                                    Step {currentIdentifyingIndex + 1} of {physicalDriveCount}
                                </div>
                                <h3 className="text-2xl font-black text-white">Identify Physical Drive {currentIdentifyingIndex}</h3>
                                <p className="text-xs text-[#71717a] max-w-xs leading-relaxed">
                                    Clicking identify will move tape <strong>{selectedBarcode}</strong> to
                                    Data Transfer Element <strong>{currentIdentifyingIndex}</strong>.
                                </p>
                            </div>

                            {isProcessing ? (
                                <div className="flex flex-col items-center gap-4 animate-in fade-in zoom-in">
                                    <div className="relative">
                                        <div className="size-20 border-4 border-primary/20 border-t-primary rounded-full animate-spin" />
                                        <Loader2 className="size-10 text-primary absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
                                    </div>
                                    <div className="space-y-1">
                                        <div className="text-[10px] font-bold text-primary uppercase tracking-[0.2em] animate-pulse">Running mechanical diagnostics...</div>
                                        <div className="text-[9px] text-[#71717a] font-mono">Poll: tapeinfo -f /dev/nstX</div>
                                    </div>
                                </div>
                            ) : (
                                <button
                                    onClick={handleIdentify}
                                    className="group relative flex items-center gap-3 px-8 py-4 bg-primary text-black font-black uppercase tracking-widest rounded-md shadow-glow hover:bg-green-400 transition-all hover:scale-105"
                                >
                                    <Play className="size-5 fill-black" />
                                    Identify Drive {currentIdentifyingIndex}
                                </button>
                            )}

                            {mapping[currentIdentifyingIndex] && (
                                <div className="flex items-center gap-2 px-4 py-2 bg-green-500/10 border border-green-500/20 rounded-full text-green-500 text-[10px] font-bold">
                                    <CheckCircle2 className="size-3" />
                                    FOUND: {mapping[currentIdentifyingIndex]}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Step: Review */}
                    {step === 'review' && (
                        <div className="flex-1 space-y-6">
                            <div className="text-center">
                                <h3 className="text-lg font-bold text-white">Review Final Mapping</h3>
                                <p className="text-xs text-[#71717a]">Ensure these logical devices correctly correspond to the physical drives.</p>
                            </div>

                            <div className="space-y-2">
                                {Object.entries(mapping).map(([mtx, logical]) => (
                                    <div key={mtx} className="flex items-center justify-between p-4 bg-[#09090b] border border-[#27272a] rounded-lg">
                                        <div className="flex items-center gap-3">
                                            <div className="size-8 rounded bg-[#27272a] flex items-center justify-center font-bold text-xs text-white">
                                                {mtx}
                                            </div>
                                            <div>
                                                <div className="text-[10px] font-bold text-[#71717a] uppercase tracking-widest mb-0.5">Physical Drive {mtx}</div>
                                                <div className="text-xs font-mono text-white">{logical}</div>
                                            </div>
                                        </div>
                                        <CheckCircle2 className="size-5 text-primary" />
                                    </div>
                                ))}
                            </div>

                            <div className="p-4 bg-yellow-500/5 border border-yellow-500/20 rounded-lg flex gap-3">
                                <Info className="size-5 text-yellow-500/80 shrink-0" />
                                <p className="text-[10px] text-yellow-500/70 leading-relaxed font-medium">
                                    Saving these changes will update <code>config.json</code>. You must restart the FossilSafe service manually or via the System tab for these changes to take effect.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Step: Success */}
                    {step === 'success' && (
                        <div className="flex-1 flex flex-col items-center justify-center space-y-6 text-center py-4">
                            <div className="size-20 bg-primary/10 rounded-full flex items-center justify-center border border-primary/20 animate-in zoom-in duration-500">
                                <CheckCircle2 className="size-10 text-primary" />
                            </div>
                            <div className="space-y-2">
                                <h3 className="text-2xl font-black text-white">Calibration Complete</h3>
                                <p className="text-sm text-[#71717a] max-w-sm">
                                    Physical drive mappings have been persistsed to disk.
                                    Your multi-drive setup is now ready for production.
                                </p>
                            </div>
                            <button
                                onClick={handleClose}
                                className="px-6 py-2 bg-white text-black text-[10px] font-bold uppercase tracking-widest rounded hover:bg-[#a1a1aa] transition-colors"
                            >
                                Back to Appliance
                            </button>
                        </div>
                    )}
                </div>

                {/* Footer */}
                {step !== 'success' && (
                    <div className="flex items-center justify-between px-6 py-4 border-t border-[#27272a] bg-[#18181b]">
                        <button
                            onClick={handleClose}
                            className="text-[10px] font-bold uppercase tracking-widest text-[#71717a] hover:text-white"
                        >
                            Cancel
                        </button>

                        <div className="flex gap-3">
                            {step === 'prepare' && (
                                <button
                                    onClick={() => setStep('identify')}
                                    disabled={!selectedBarcode}
                                    className={cn(
                                        "px-6 py-2 bg-primary text-black text-[10px] font-bold uppercase tracking-widest rounded-md shadow-glow transition-all",
                                        !selectedBarcode && "opacity-50 blur-[2px] grayscale"
                                    )}
                                >
                                    Start Mapping
                                </button>
                            )}
                            {step === 'welcome' && (
                                <button
                                    onClick={() => setStep('prepare')}
                                    className="px-6 py-2 bg-primary text-black text-[10px] font-bold uppercase tracking-widest rounded-md shadow-glow"
                                >
                                    Get Started
                                </button>
                            )}
                            {step === 'review' && (
                                <button
                                    onClick={handleSave}
                                    disabled={isLoading}
                                    className="px-6 py-2 bg-primary text-black text-[10px] font-bold uppercase tracking-widest rounded-md shadow-glow flex items-center gap-2"
                                >
                                    {isLoading ? <Loader2 className="size-3 animate-spin" /> : <Save className="size-3" />}
                                    Save Config
                                </button>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
