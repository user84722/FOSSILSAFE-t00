import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api, type Tape } from "@/lib/api"
import {
    ArchiveRestore,
    X,
    CassetteTape,
    CheckSquare,
    Info,
    Key,
    ArrowLeft,
    ArrowRight
} from "lucide-react"

interface RestoreWizardProps {
    isOpen: boolean
    onClose: () => void
    onSuccess?: () => void
}

type WizardStep = 'SOURCE' | 'BROWSE' | 'DESTINATION' | 'CONFIRM'

export default function RestoreWizard({ isOpen, onClose, onSuccess }: RestoreWizardProps) {
    const [step, setStep] = useState<WizardStep>('SOURCE')
    const [tapes, setTapes] = useState<Tape[]>([])
    const [selectedTape, setSelectedTape] = useState<Tape | null>(null)
    const [selectedFiles, setSelectedFiles] = useState<string[]>([])
    const [destinationPath, setDestinationPath] = useState('/mnt/restore')
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState('')
    const [hardwarePrompt, setHardwarePrompt] = useState<{ barcode: string, isOpen: boolean }>({ barcode: '', isOpen: false })
    const [pollingInterval, setPollingInterval] = useState<NodeJS.Timeout | null>(null)
    const [randomVerification, setRandomVerification] = useState(false)
    const [randomSampleSize, setRandomSampleSize] = useState(10)

    // Browse step state
    const [searchQuery, setSearchQuery] = useState('')
    const [availableFiles, setAvailableFiles] = useState<any[]>([])
    const [isFetchingFiles, setIsFetchingFiles] = useState(false)

    // Fetch tapes when wizard opens
    useEffect(() => {
        if (isOpen) {
            fetchTapes()
        }
        return () => stopPolling()
    }, [isOpen])

    // Fetch files when entering Browse step
    useEffect(() => {
        if (step === 'BROWSE' && selectedTape) {
            fetchFiles()
        }
    }, [step, selectedTape])

    const fetchFiles = async () => {
        if (!selectedTape) return
        setIsFetchingFiles(true)
        try {
            const res = await api.searchFiles({
                query: searchQuery,
                tape_barcode: selectedTape.barcode
            })
            if (res.success && res.data) {
                setAvailableFiles(res.data.files)
            }
        } catch (err) {
            console.error("Failed to fetch files", err)
        } finally {
            setIsFetchingFiles(false)
        }
    }

    const fetchTapes = async () => {
        try {
            const res = await api.getTapes()
            if (res.success && res.data) {
                // Show ALL tapes, including offline
                setTapes(res.data.tapes)
            }
        } catch (err) {
            console.error("Failed to fetch tapes", err)
        }
    }

    const stopPolling = () => {
        if (pollingInterval) {
            clearInterval(pollingInterval)
            setPollingInterval(null)
        }
    }

    const startPollingForTape = (barcode: string) => {
        stopPolling()
        const interval = setInterval(async () => {
            const res = await api.getTapes()
            if (res.success && res.data) {
                const tape = res.data.tapes.find(t => t.barcode === barcode)
                if (tape && tape.status !== 'offline') {
                    // Tape found!
                    stopPolling()
                    setHardwarePrompt({ barcode: '', isOpen: false })
                    setTapes(res.data.tapes)
                    setSelectedTape(tape)
                }
            }
        }, 5000)
        setPollingInterval(interval)
    }

    const handleNext = () => {
        if (step === 'SOURCE') setStep('BROWSE')
        else if (step === 'BROWSE') setStep('DESTINATION')
        else if (step === 'DESTINATION') setStep('CONFIRM')
    }

    const handleBack = () => {
        if (step === 'BROWSE') setStep('SOURCE')
        else if (step === 'DESTINATION') setStep('BROWSE')
        else if (step === 'CONFIRM') setStep('DESTINATION')
    }

    const handleRestore = async () => {
        if (!selectedTape) return

        setIsLoading(true)
        setError('')

        try {
            const res = await api.createJob({
                type: 'restore',
                name: `RESTORE_${selectedTape.barcode}_${Date.now()}`,
                target_tape: selectedTape.barcode,
                source_path: selectedFiles.join(','), // Simplified file list
                status: 'pending',
                progress: 0,
                created_at: new Date().toISOString()
            })

            if (res.success) {
                onSuccess?.()
                onClose()
            } else {
                setError(res.error || 'Failed to start restore job')
            }
        } catch (err) {
            setError('An error occurred during job creation')
        } finally {
            setIsLoading(false)
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/80 backdrop-blur-sm"
                onClick={onClose}
            />

            {/* Wizard Modal */}
            <div className="relative w-full max-w-4xl bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden flex flex-col h-[600px]">
                {/* Header */}
                <div className="px-8 py-5 border-b border-[#27272a] flex items-center justify-between bg-[#18181b]/50">
                    <div className="flex items-center gap-3">
                        <ArchiveRestore className="text-primary size-5" />
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Restore Data Flow</h2>
                            <p className="text-[10px] text-[#71717a] font-mono uppercase">Media Retrieval Sequence</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="text-[#71717a] hover:text-white transition-colors">
                        <X className="size-5" />
                    </button>
                </div>

                {/* Progress Bar */}
                <div className="h-1 w-full bg-black/40 flex">
                    <div className={cn("h-full bg-primary transition-all duration-500",
                        step === 'SOURCE' ? 'w-1/4' :
                            step === 'BROWSE' ? 'w-2/4' :
                                step === 'DESTINATION' ? 'w-3/4' : 'w-full'
                    )} />
                </div>

                {/* Content Area */}
                <div className="flex-1 overflow-hidden flex flex-col">
                    {step === 'SOURCE' && (
                        <div className="p-8 space-y-6 flex-1 overflow-y-auto">
                            <div className="space-y-2">
                                <h3 className="text-xs font-bold text-white uppercase tracking-widest">Select Source Medium</h3>
                                <p className="text-[10px] text-[#71717a]">Identify the tape cartridge containing the required archive sets.</p>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                {tapes.map((tape) => (
                                    <div
                                        key={tape.barcode}
                                        onClick={() => {
                                            if (tape.status === 'offline') {
                                                setHardwarePrompt({ barcode: tape.barcode, isOpen: true })
                                                startPollingForTape(tape.barcode)
                                            } else {
                                                setSelectedTape(tape)
                                            }
                                        }}
                                        className={cn(
                                            "p-4 rounded border cursor-pointer transition-all group relative",
                                            selectedTape?.barcode === tape.barcode
                                                ? "bg-primary/5 border-primary shadow-[0_0_15px_rgba(25,230,100,0.1)]"
                                                : tape.status === 'offline'
                                                    ? "bg-[#18181b] border-[#27272a] opacity-70 hover:opacity-100 hover:border-[#52525b]"
                                                    : "bg-black/20 border-[#27272a] hover:border-[#3f3f46]"
                                        )}
                                    >
                                        <div className="flex items-center gap-4">
                                            <CassetteTape className={cn(
                                                "size-8 transition-colors",
                                                selectedTape?.barcode === tape.barcode ? "text-primary" :
                                                    tape.status === 'offline' ? "text-[#52525b]" : "text-[#3f3f46] group-hover:text-[#71717a]"
                                            )} />
                                            <div className="flex-1">
                                                <div className="flex items-center justify-between">
                                                    <div className="text-[11px] font-bold text-white font-mono">{tape.barcode}</div>
                                                    {tape.status === 'offline' && (
                                                        <span className="text-[9px] font-bold text-[#52525b] uppercase tracking-wider bg-[#27272a] px-1.5 py-0.5 rounded">Offline</span>
                                                    )}
                                                </div>
                                                <div className="text-[9px] text-[#71717a] uppercase tracking-widest mt-0.5">{tape.alias || 'No Alias'}</div>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>

                            {/* Hardware Guidance Modal */}
                            {hardwarePrompt.isOpen && (
                                <div className="absolute inset-0 z-50 bg-black/90 backdrop-blur-md flex flex-col items-center justify-center p-8 text-center animate-in fade-in">
                                    <div className="size-16 rounded-full bg-primary/10 flex items-center justify-center mb-6 animate-pulse">
                                        <CassetteTape className="size-8 text-primary" />
                                    </div>
                                    <h3 className="text-lg font-bold text-white uppercase tracking-widest mb-2">Insert Tape Required</h3>
                                    <p className="text-xs text-[#71717a] max-w-sm mb-8 leading-relaxed">
                                        The selected archive volume <span className="text-white font-mono font-bold">{hardwarePrompt.barcode}</span> is currently offline.
                                        Please insert the cartridge into the mail slot or any available drive to continue.
                                    </p>

                                    <div className="flex items-center gap-2 mb-8 px-4 py-2 bg-[#18181b] rounded border border-[#27272a]">
                                        <div className="size-2 rounded-full bg-primary animate-ping"></div>
                                        <span className="text-[10px] font-mono text-primary uppercase">Scanning Library...</span>
                                    </div>

                                    <button
                                        onClick={() => {
                                            stopPolling()
                                            setHardwarePrompt({ barcode: '', isOpen: false })
                                        }}
                                        className="px-6 py-2 border border-[#27272a] hover:bg-[#27272a] text-[#71717a] hover:text-white text-xs font-bold uppercase tracking-widest rounded transition-colors"
                                    >
                                        Cancel Detection
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {step === 'BROWSE' && (
                        <div className="p-8 space-y-6 flex-1 flex flex-col overflow-hidden">
                            <div className="space-y-2">
                                <h3 className="text-xs font-bold text-white uppercase tracking-widest">Browse Archive Contents</h3>
                                <p className="text-[10px] text-[#71717a]">Select specific files or directories to restore from <span className="text-white font-mono">{selectedTape?.barcode}</span>.</p>
                            </div>

                            {/* Search Input */}
                            <div className="relative">
                                <span className="absolute left-3 top-1/2 -translate-y-1/2 material-symbols-outlined text-[#71717a] text-sm">search</span>
                                <input
                                    type="text"
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-9 py-2 text-[11px] font-mono text-white focus:border-primary/50 outline-none transition-all"
                                    placeholder="Search files..."
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Enter' && fetchFiles()}
                                />
                            </div>

                            <div className="flex-1 bg-black/40 border border-[#27272a] rounded overflow-hidden flex flex-col">
                                {isFetchingFiles ? (
                                    <div className="flex-1 flex flex-col items-center justify-center p-4">
                                        <div className="size-4 border-2 border-primary border-t-transparent rounded-full animate-spin mb-2" />
                                        <p className="text-[10px] text-[#71717a] font-mono">Indexing tape contents...</p>
                                    </div>
                                ) : availableFiles.length > 0 ? (
                                    <div className="flex-1 overflow-y-auto p-2 space-y-1">
                                        {availableFiles.map((file) => (
                                            <div
                                                key={file.id}
                                                className={cn(
                                                    "flex items-center gap-3 p-2 rounded cursor-pointer transition-colors group",
                                                    selectedFiles.includes(file.file_path)
                                                        ? "bg-primary/10 text-white"
                                                        : "hover:bg-[#27272a] text-[#a1a1aa]"
                                                )}
                                                onClick={() => {
                                                    if (selectedFiles.includes(file.file_path)) setSelectedFiles(f => f.filter(x => x !== file.file_path))
                                                    else setSelectedFiles(f => [...f, file.file_path])
                                                }}
                                            >
                                                <div className={cn(
                                                    "size-4 rounded-sm border flex items-center justify-center transition-all flex-shrink-0",
                                                    selectedFiles.includes(file.file_path) ? "bg-primary border-primary text-black" : "border-[#3f3f46] group-hover:border-[#52525b]"
                                                )}>
                                                    {selectedFiles.includes(file.file_path) && <CheckSquare className="size-3" />}
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <div className="text-[10px] font-mono truncate">{file.file_path}</div>
                                                    <div className="flex items-center gap-3 mt-0.5">
                                                        <span className="text-[9px] text-[#52525b] uppercase tracking-wider">{file.file_size ? (file.file_size / 1024 / 1024).toFixed(2) + ' MB' : '0 B'}</span>
                                                        <span className="text-[9px] text-[#52525b] uppercase tracking-wider">{new Date(file.file_mtime || file.archived_at).toLocaleDateString()}</span>
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="flex-1 flex flex-col items-center justify-center p-4 text-center">
                                        <span className="material-symbols-outlined text-[#27272a] text-3xl mb-2">folder_off</span>
                                        <p className="text-[10px] text-[#52525b] font-mono uppercase">No matching assets found</p>
                                    </div>
                                )}
                            </div>

                            <div className="flex justify-between items-center px-1">
                                <p className="text-[9px] text-[#52525b] font-mono uppercase tracking-wider">
                                    {selectedFiles.length} Selected
                                </p>
                                <p className="text-[9px] text-[#52525b] font-mono uppercase tracking-wider">
                                    {availableFiles.length} Available (Limit 1000)
                                </p>
                            </div>
                        </div>
                    )}

                    {step === 'DESTINATION' && (
                        <div className="p-8 space-y-8 flex-1 overflow-y-auto">
                            <div className="space-y-2">
                                <h3 className="text-xs font-bold text-white uppercase tracking-widest">Deployment Destination</h3>
                                <p className="text-[10px] text-[#71717a]">Specify the target directory for the restored data assets.</p>
                            </div>

                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] px-1">Target Path</label>
                                    <input
                                        type="text"
                                        value={destinationPath}
                                        onChange={(e) => setDestinationPath(e.target.value)}
                                        className="w-full bg-black/60 border border-[#27272a] rounded px-4 py-3 text-xs font-mono text-white outline-none focus:border-primary/50 transition-all"
                                    />
                                </div>

                                {/* Random Verification Option */}
                                <div className="space-y-3 p-4 rounded bg-black/40 border border-[#27272a]">
                                    <div className="flex items-center gap-3">
                                        <input
                                            type="checkbox"
                                            id="random-verification"
                                            checked={randomVerification}
                                            onChange={(e) => setRandomVerification(e.target.checked)}
                                            className="size-4 rounded border-[#27272a] bg-black/60 text-primary focus:ring-primary/50"
                                        />
                                        <label htmlFor="random-verification" className="text-xs font-bold text-white uppercase tracking-wider cursor-pointer">
                                            Enable Random Verification Sampling
                                        </label>
                                    </div>

                                    {randomVerification && (
                                        <div className="pl-7 space-y-2 animate-in slide-in-from-top-2">
                                            <p className="text-[10px] text-[#71717a] leading-relaxed">
                                                Randomly select and verify a subset of files to ensure restore integrity without processing the entire archive.
                                            </p>
                                            <div className="flex items-center gap-3">
                                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-wider">Sample Size:</label>
                                                <input
                                                    type="number"
                                                    min="1"
                                                    max="100"
                                                    value={randomSampleSize}
                                                    onChange={(e) => setRandomSampleSize(parseInt(e.target.value) || 10)}
                                                    className="w-20 bg-black/60 border border-[#27272a] rounded px-3 py-1.5 text-xs font-mono text-white outline-none focus:border-primary/50 transition-all"
                                                />
                                                <span className="text-[10px] text-[#71717a]">files</span>
                                            </div>
                                        </div>
                                    )}
                                </div>

                                <div className="p-4 rounded bg-primary/5 border border-primary/20 flex gap-4 items-start">
                                    <Info className="text-primary size-3.5 mt-0.5" />
                                    <p className="text-[10px] text-[#71717a] leading-relaxed">
                                        Data will be restored with original permissions. Ensure the target filesystem has sufficient capacity for approx. <span className="text-white">4.2 TB</span>.
                                    </p>
                                </div>
                            </div>
                        </div>
                    )}

                    {step === 'CONFIRM' && (
                        <div className="p-8 space-y-8 flex-1 overflow-y-auto">
                            <div className="space-y-2 text-center pb-4">
                                <h3 className="text-xs font-bold text-white uppercase tracking-widest">Mission Confirmation</h3>
                                <p className="text-[10px] text-[#71717a]">Verify the retrieval parameters before initiating extraction.</p>
                            </div>

                            <div className="max-w-md mx-auto space-y-4">
                                <div className="bg-[#09090b] border border-[#27272a] rounded overflow-hidden">
                                    <div className="px-4 py-3 bg-[#18181b] border-b border-[#27272a] flex justify-between items-center">
                                        <span className="text-[8px] font-bold text-[#71717a] uppercase tracking-widest">Parameter</span>
                                        <span className="text-[8px] font-bold text-[#71717a] uppercase tracking-widest">Value</span>
                                    </div>
                                    <div className="p-4 space-y-4">
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] text-[#71717a] uppercase font-bold tracking-widest">Source Volume</span>
                                            <span className="text-[11px] text-white font-mono">{selectedTape?.barcode}</span>
                                        </div>
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] text-[#71717a] uppercase font-bold tracking-widest">Deployment Target</span>
                                            <span className="text-[11px] text-white font-mono">{destinationPath}</span>
                                        </div>
                                        <div className="flex justify-between items-center">
                                            <span className="text-[10px] text-[#71717a] uppercase font-bold tracking-widest">Asset Count</span>
                                            <span className="text-[11px] text-white font-mono">{selectedFiles.length} Selections</span>
                                        </div>
                                    </div>

                                    {/* Encryption Warning */}
                                    <div className="px-4 py-3 bg-blue-500/5 border-t border-blue-500/10 flex gap-3">
                                        <Key className="text-blue-400 size-3.5 mt-0.5" />
                                        <p className="text-[10px] text-blue-400/80 leading-relaxed font-medium">
                                            If the source media is encrypted, ensure the matching <span className="text-white font-mono bg-blue-500/20 px-1 rounded">credential_key.bin</span> is present in the system keystore.
                                        </p>
                                    </div>
                                </div>
                            </div>

                            {error && (
                                <div className="p-3 bg-destructive/10 border border-destructive/20 text-destructive text-[10px] font-mono text-center rounded">
                                    {error}
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="px-8 py-6 bg-[#09090b]/50 border-t border-[#27272a] flex justify-between items-center">
                    <button
                        onClick={step === 'SOURCE' ? onClose : handleBack}
                        className="px-6 py-2.5 text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-widest transition-colors flex items-center gap-2"
                    >
                        <span className="material-symbols-outlined text-sm">{step === 'SOURCE' ? <X className="size-3.5" /> : <ArrowLeft className="size-3.5" />}</span>
                        {step === 'SOURCE' ? 'Abort' : 'Previous Phase'}
                    </button>

                    <button
                        onClick={step === 'CONFIRM' ? handleRestore : handleNext}
                        disabled={isLoading || (step === 'SOURCE' && !selectedTape) || (step === 'BROWSE' && selectedFiles.length === 0)}
                        className={cn(
                            "px-8 py-2.5 bg-primary hover:bg-green-400 text-black text-[10px] font-bold uppercase tracking-widest rounded transition-all flex items-center gap-2",
                            (isLoading || (step === 'SOURCE' && !selectedTape) || (step === 'BROWSE' && selectedFiles.length === 0)) && "opacity-50 cursor-not-allowed"
                        )}
                    >
                        {isLoading ? 'Processing...' : step === 'CONFIRM' ? 'Initiate Extraction' : 'Proceed to Next Phase'}
                        {!isLoading && <ArrowRight className="size-3.5" />}
                    </button>
                </div>
            </div>
        </div>
    )
}
