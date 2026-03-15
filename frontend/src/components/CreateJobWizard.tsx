import { useState, useEffect } from "react"
import { Link } from "react-router-dom"

import { cn, format_bytes } from "@/lib/utils"
import { api, type Tape } from "@/lib/api"
import { useDemoMode } from "@/lib/demoMode"
import BrowserUploadStep from "./BrowserUploadStep"
import { TapeSelect } from "./TapeSelect"

type JobType = 'archive' | 'backup' | 'restore' | 'verify'
type WizardStep = 'type' | 'source' | 'destination' | 'hooks' | 'preflight' | 'confirm'
interface CreateJobWizardProps {
    isOpen: boolean
    onClose: () => void
    onSuccess?: (jobId: number) => void
    tapes?: Tape[]
}

const JOB_TYPES: Array<{ id: JobType; label: string; icon: string; description: string; detail: string; risk: 'low' | 'high' | 'neutral' }> = [
    {
        id: 'archive',
        label: 'Archive (Move)',
        icon: 'inventory_2',
        description: 'Move data to tape (Delete source)',
        detail: 'MOVES data to tape. FILES ARE REMOVED from source after verification. Use for long-term storage of finished projects.',
        risk: 'high'
    },
    {
        id: 'backup',
        label: 'Backup (Copy)',
        icon: 'backup',
        description: 'Copy data to tape (Keep source)',
        detail: 'COPIES data to tape. FILES REMAIN on source. Use for daily protection and disaster recovery.',
        risk: 'low'
    },
    {
        id: 'restore',
        label: 'Restore',
        icon: 'settings_backup_restore',
        description: 'Recover data from tape to disk',
        detail: 'Restores selected files or full tapes back to a specified disk location.',
        risk: 'neutral'
    },
    {
        id: 'verify',
        label: 'Verify',
        icon: 'verified',
        description: 'Check tape integrity',
        detail: 'Scans tape contents to ensure data is readable and checksums match the catalog.',
        risk: 'neutral'
    },
]

export default function CreateJobWizard({ isOpen, onClose, onSuccess, tapes = [] }: CreateJobWizardProps) {
    const [step, setStep] = useState<WizardStep>('type')
    const [jobType, setJobType] = useState<JobType | null>(null)
    const [sourcePath, setSourcePath] = useState('')
    const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null)
    const [sourceTab, setSourceTab] = useState<'managed' | 'manual' | 'upload'>('managed')
    const [uploadGroupId, setUploadGroupId] = useState('')
    const [sources, setSources] = useState<any[]>([])
    const [selectedTape, setSelectedTape] = useState('')
    const [compression, setCompression] = useState(true)
    const [encryption, setEncryption] = useState<'none' | 'software' | 'hardware'>('none')
    const [encryptionPassword, setEncryptionPassword] = useState('')
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState('')
    const [confirmArchive, setConfirmArchive] = useState(false)
    const [preflightResults, setPreflightResults] = useState<any>(null)
    const [isPreflighting, setIsPreflighting] = useState(false)
    const [availableHooks, setAvailableHooks] = useState<{ pre: any[], post: any[] }>({ pre: [], post: [] })
    const [preHook, setPreHook] = useState('')
    const [postHook, setPostHook] = useState('')
    const [estimatedSize, setEstimatedSize] = useState<number | null>(null)
    const [capacityResult, setCapacityResult] = useState<any>(null)
    const [isValidatingCapacity, setIsValidatingCapacity] = useState(false)
    const { isDemoMode } = useDemoMode()

    // Fetch sources when wizard opens
    useEffect(() => {
        if (isOpen) {
            setUploadGroupId(Math.random().toString(36).substring(7))
            if (isDemoMode) {
                setSources([
                    {
                        id: 1,
                        name: 'Production Share',
                        type: 'smb',
                        config: { path: '//nas-01/production' }
                    },
                    {
                        id: 2,
                        name: 'Media Assets',
                        type: 's3',
                        config: { bucket: 'corporate-media-assets' }
                    }
                ])
            } else {
                api.getSources().then(res => {
                    if (res.success && res.data) {
                        setSources(res.data.sources)
                    }
                })
            }
        }
    }, [isOpen, isDemoMode])

    // Estimate size when sourcePath changes (with debounce)
    useEffect(() => {
        if (!sourcePath || jobType === 'restore' || jobType === 'verify') {
            setEstimatedSize(null)
            return
        }

        const timer = setTimeout(async () => {
            try {
                const res = await api.estimatePathSize(sourcePath)
                if (res.success && res.data) {
                    setEstimatedSize(res.data.total_size)
                }
            } catch (e) {
                console.error("Failed to estimate size", e)
            }
        }, 1000)

        return () => clearTimeout(timer)
    }, [sourcePath, jobType])

    // Check capacity when estimatedSize or selectedTape changes
    useEffect(() => {
        if (estimatedSize === null || !selectedTape || jobType === 'restore' || jobType === 'verify') {
            setCapacityResult(null)
            return
        }

        const runCheck = async () => {
            setIsValidatingCapacity(true)
            try {
                const res = await api.checkTapeCapacity(estimatedSize, [selectedTape])
                if (res.success && res.data) {
                    setCapacityResult(res.data.result)
                }
            } catch (e) {
                console.error("Failed to check capacity", e)
            } finally {
                setIsValidatingCapacity(false)
            }
        }

        runCheck()
    }, [estimatedSize, selectedTape, jobType])

    const steps: WizardStep[] = ['type', 'source', 'destination', 'hooks', 'preflight', 'confirm']
    const currentStepIndex = steps.indexOf(step)
    const progress = ((currentStepIndex + 1) / steps.length) * 100

    const resetWizard = () => {
        setStep('type')
        setJobType(null)
        setSourcePath('')
        setSelectedSourceId(null)
        setSourceTab('managed')
        setSelectedTape('')
        setCompression(true)
        setEncryption('none')
        setEncryptionPassword('')
        setConfirmArchive(false)
        setPreflightResults(null)
        setIsPreflighting(false)
        setPreHook('')
        setPostHook('')
        setError('')
    }

    const handleClose = () => {
        resetWizard()
        onClose()
    }

    const canProceed = (): boolean => {
        switch (step) {
            case 'type': return jobType !== null
            case 'source':
                if (jobType === 'restore' || jobType === 'verify') return selectedTape.length > 0
                return sourcePath.trim().length > 0
            case 'destination':
                if (jobType === 'restore') return sourcePath.trim().length > 0
                return selectedTape.length > 0
            case 'hooks':
                return true
            case 'preflight':
                return preflightResults !== null
            case 'confirm':
                if (encryption === 'software' && !encryptionPassword) return false
                return jobType === 'archive' ? confirmArchive : true
            default: return false
        }
    }

    const handleNextStep = async () => {
        if (step === 'source' && (jobType === 'backup' || jobType === 'archive')) {
            // Run Source Validation
            setIsLoading(true)
            setError('')
            try {
                // Use dry-run as a lightweight pre-flight for source validation
                console.log("CACHE BUSTER 2026-03-12");
                const res = await api.runPreflight({
                    source_path: sourcePath,
                    type: jobType
                })

                if (!res.success) {
                    throw new Error(res.error || "Failed to validate source")
                }

                if (res.data && res.data.result) {
                    if (!res.data.result.valid) {
                        const errorMsg = res.data.result.errors?.length 
                            ? res.data.result.errors.join(", ") 
                            : res.data.result.message || "Invalid source configuration";
                        throw new Error(errorMsg)
                    }
                }

                handleChangeStep(1)
            } catch (e: any) {
                setError(e.message || "Failed to validate source")
            } finally {
                setIsLoading(false)
            }
        } else if (step === 'destination' && (jobType === 'backup' || jobType === 'archive')) {
            // Fetch hooks before moving to hooks step
            handleFetchHooks()
        } else if (step === 'hooks') {
            // Run Dry Run / Preflight
            handleRunPreflight()
        } else if (step === 'source' && jobType === 'restore') {
            // Optimization: If it's a restore, we might want to pre-check tape availability
            // But for now just proceed to destination picker
            handleChangeStep(1)
        } else {
            handleChangeStep(1)
        }
    }

    const handleFetchHooks = async () => {
        setIsLoading(true)
        try {
            const res = await api.getHooks()
            if (res.success && res.data) {
                setAvailableHooks(res.data)
                setStep('hooks')
            } else {
                setStep('hooks') // Still proceed even if fetch fails
            }
        } catch (e) {
            setStep('hooks')
        } finally {
            setIsLoading(false)
        }
    }

    const handleRunPreflight = async () => {
        setIsPreflighting(true)
        setError('')
        setPreflightResults(null)
        try {
            const res = await api.runDryRun({
                source_id: selectedSourceId?.toString(),
                source_path: sourcePath,
                tape_barcode: selectedTape,
                compression: compression ? 'zstd' : 'none',
                pre_job_hook: preHook,
                post_job_hook: postHook
            })

            if (res.success && res.data) {
                setPreflightResults(res.data)
                setStep('preflight')
            } else {
                throw new Error(res.error || "Preflight failed")
            }
        } catch (e: any) {
            setError(e.message || "Preflight failed")
        } finally {
            setIsPreflighting(false)
        }
    }

    const handleChangeStep = (direction: number) => {
        setError('')
        const idx = steps.indexOf(step)
        const newIdx = idx + direction
        if (newIdx >= 0 && newIdx < steps.length) {
            setStep(steps[newIdx])
        }
    }

    const [driveBusy, setDriveBusy] = useState(false)

    // Check drive status when entering confirm step
    useEffect(() => {
        if (step === 'confirm') {
            checkDriveStatus()
        }
    }, [step])

    const checkDriveStatus = async () => {
        try {
            const res = await api.getDriveHealth()
            if (res.success && res.data?.drives) {
                const drive0 = res.data.drives.find(d => d.id === '0' || d.id === '0') // Ensure string comparison
                if (drive0 && (drive0.status === 'busy' || drive0.status === 'reading' || drive0.status === 'writing')) {
                    setDriveBusy(true)
                } else {
                    setDriveBusy(false)
                }
            }
        } catch (e) {
            console.error("Failed to check drive status", e)
        }
    }

    const handleFormatTape = async (barcode: string) => {
        if (!confirm(`Are you sure you want to format tape ${barcode}? This will erase all data.`)) return

        setIsLoading(true)
        try {
            const res = await api.formatTape(barcode)
            if (res.success) {
                // Refresh tapes? relying on parent to refresh or just optimistic update?
                // For now just show success
                alert("Format started. Tape will be available shortly.")
            } else {
                throw new Error(res.error || "Format failed")
            }
        } catch (e: any) {
            alert(e.message)
        } finally {
            setIsLoading(false)
        }
    }

    const handleCreateJob = async () => {
        if (!jobType) return

        setIsLoading(true)
        setError('')

        try {
            if (jobType === 'restore') {
                // For a full tape restore via wizard
                const res = await api.initiateRestore({
                    files: [],
                    destination: sourcePath, // We use step 3's path
                    tape_barcode: selectedTape,
                    restoreType: 'file',
                    confirm: true,
                    overwrite: false
                })
                if (res.success && res.data) {
                    onSuccess?.(res.data.restore_id)
                    handleClose()
                } else {
                    throw new Error(res.error || 'Failed to initiate restore')
                }
            } else {
                const res = await api.createJob({
                    job_type: jobType,
                    name: `${jobType}_${Date.now()}`,
                    source_id: selectedSourceId?.toString() || '',
                    tapes: [selectedTape],
                    compression: compression ? 'zstd' : 'none',
                    encryption: encryption,
                    encryption_password: encryptionPassword,
                    archival_policy: jobType === 'archive' ? 'delete_source' : 'none',
                    preflight_passed: true,
                    pre_job_hook: preHook,
                    post_job_hook: postHook
                })

                if (res.success && res.data) {
                    onSuccess?.(res.data.job_id)
                    handleClose()
                } else {
                    throw new Error(res.error || 'Failed to create job')
                }
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to create job')
        } finally {
            setIsLoading(false)
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/80 backdrop-blur-sm"
                onClick={handleClose}
            />

            {/* Modal */}
            <div className="relative w-full max-w-2xl mx-4 bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden flex flex-col">
                {/* Progress Bar */}
                <div className="h-1 bg-[#27272a]">
                    <div
                        className="h-full bg-primary transition-all duration-300"
                        style={{ width: `${progress}%` }}
                    />
                </div>

                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#27272a]">
                    <div className="flex items-center gap-3">
                        <span className="material-symbols-outlined text-primary">add_task</span>
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Create New Job</h2>
                            <p className="text-[10px] text-[#71717a] font-mono uppercase">Step {currentStepIndex + 1} of {steps.length}</p>
                        </div>
                    </div>
                    <button
                        onClick={handleClose}
                        className="text-[#71717a] hover:text-white transition-colors"
                    >
                        <span className="material-symbols-outlined">close</span>
                    </button>
                </div>

                {/* Content */}
                <div className="p-6 min-h-[320px] overflow-y-auto max-h-[70vh]">
                    {error && (
                        <div className="mb-4 p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs">
                            {error}
                        </div>
                    )}

                    {/* Step 1: Job Type */}
                    {step === 'type' && (
                        <div className="space-y-4">
                            <div className="text-center mb-6">
                                <h3 className="text-lg font-semibold text-white">Select Job Type</h3>
                                <p className="text-xs text-[#71717a] mt-1">Choose the type of operation to perform</p>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                {JOB_TYPES.map((type) => (
                                    <button
                                        key={type.id}
                                        onClick={() => setJobType(type.id)}
                                        className={cn(
                                            "p-4 rounded-lg border text-left transition-all group relative overflow-hidden",
                                            jobType === type.id
                                                ? "bg-primary/10 border-primary/50 ring-1 ring-primary/30"
                                                : "bg-[#09090b] border-[#27272a] hover:border-[#3f3f46]"
                                        )}
                                    >
                                        {/* Risk Badge */}
                                        {type.risk !== 'neutral' && (
                                            <div className={cn(
                                                "absolute top-2 right-2 px-1.5 py-0.5 rounded text-[7px] font-bold uppercase tracking-widest border",
                                                type.risk === 'high' ? "bg-destructive/10 border-destructive/20 text-destructive" : "bg-primary/10 border-primary/20 text-primary"
                                            )}>
                                                {type.risk === 'high' ? 'Destructive' : 'Safe'}
                                            </div>
                                        )}

                                        <span className={cn(
                                            "material-symbols-outlined text-2xl mb-2 block",
                                            jobType === type.id
                                                ? (type.risk === 'high' ? 'text-destructive' : 'text-primary')
                                                : "text-[#71717a] group-hover:text-white"
                                        )}>
                                            {type.icon}
                                        </span>
                                        <div className="text-sm font-bold text-white uppercase tracking-wider">{type.label}</div>
                                        <div className="text-[10px] text-[#a1a1aa] mt-1 mb-2 font-medium">{type.description}</div>

                                        {jobType === type.id && (
                                            <div className={cn(
                                                "text-[10px] leading-relaxed border-t pt-2 animate-in fade-in slide-in-from-top-1 font-medium",
                                                type.risk === 'high' ? "text-destructive/90 border-destructive/10" : "text-primary/90 border-primary/10"
                                            )}>
                                                {type.detail}
                                            </div>
                                        )}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Step 2: Source Selection */}
                    {step === 'source' && (
                        <div className="space-y-4">
                            <div className="text-center mb-6">
                                <h3 className="text-lg font-semibold text-white">
                                    {jobType === 'restore' || jobType === 'verify' ? 'Select Source Tape' : 'Select Source'}
                                </h3>
                                <p className="text-xs text-[#71717a] mt-1">
                                    {jobType === 'restore' || jobType === 'verify'
                                        ? 'Choose the tape to read from'
                                        : 'Choose a configured data source or enter a path'
                                    }
                                </p>
                            </div>

                            {jobType === 'restore' || jobType === 'verify' ? (
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Source Tape</label>
                                    <TapeSelect
                                        tapes={tapes}
                                        selectedBarcode={selectedTape}
                                        onSelect={(barcode) => setSelectedTape(barcode)}
                                        filter={(t) => (t.status === 'full' || t.status === 'in_use' || t.status === 'available') && (t.used_bytes || 0) > 0 && !t.is_cleaning_tape}
                                        className="h-[320px]"
                                    />
                                    {/* Fallback Input just in case */}
                                    <div className="flex gap-2 mt-2">
                                        <input
                                            type="text"
                                            value={selectedTape}
                                            onChange={(e) => setSelectedTape(e.target.value)}
                                            className="flex-1 bg-black/40 border border-[#27272a] rounded px-4 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none transition-all"
                                            placeholder="Or enter barcode manually..."
                                        />
                                    </div>
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    {/* Tabs and Add Source Link */}
                                    <div className="flex items-center justify-between border-b border-[#27272a] mb-4">
                                        <div className="flex">
                                            <button
                                                onClick={() => setSourceTab('managed')}
                                                className={cn(
                                                    "px-4 pb-3 text-[10px] font-bold uppercase tracking-widest border-b-2 transition-colors",
                                                    sourceTab === 'managed' ? "text-primary border-primary" : "text-[#71717a] hover:text-white border-transparent"
                                                )}
                                            >
                                                Managed Sources
                                            </button>
                                            <button
                                                onClick={() => setSourceTab('manual')}
                                                className={cn(
                                                    "px-4 pb-3 text-[10px] font-bold uppercase tracking-widest border-b-2 transition-colors",
                                                    sourceTab === 'manual' ? "text-primary border-primary" : "text-[#71717a] hover:text-white border-transparent"
                                                )}
                                            >
                                                Filesystem / Path
                                            </button>
                                            <button
                                                onClick={() => setSourceTab('upload')}
                                                className={cn(
                                                    "px-4 pb-3 text-[10px] font-bold uppercase tracking-widest border-b-2 transition-colors",
                                                    sourceTab === 'upload' ? "text-primary border-primary" : "text-[#71717a] hover:text-white border-transparent"
                                                )}
                                            >
                                                Browser Upload
                                            </button>
                                        </div>
                                        <Link
                                            to="/settings?tab=Sources"
                                            onClick={onClose}
                                            className="text-[10px] text-primary hover:text-green-300 font-bold uppercase tracking-widest flex items-center gap-1 pb-3 pr-2"
                                        >
                                            <span className="material-symbols-outlined text-[14px]">add</span>
                                            Config
                                        </Link>
                                    </div>

                                    {sourceTab === 'managed' ? (
                                        sources.length > 0 ? (
                                            <div className="grid grid-cols-1 gap-2 max-h-[250px] overflow-y-auto custom-scrollbar">
                                                {sources.map(source => (
                                                    <button
                                                        key={source.id}
                                                        onClick={() => {
                                                            setSelectedSourceId(source.id);
                                                            setSourcePath(source.config.path || (source.config.bucket ? `s3://${source.config.bucket}` : ''));
                                                        }}
                                                        className={cn(
                                                            "flex items-center gap-3 p-3 rounded border text-left transition-all",
                                                            selectedSourceId === source.id
                                                                ? "bg-primary/10 border-primary/50 ring-1 ring-primary/30 shadow-[0_0_15px_rgba(25,230,100,0.1)]"
                                                                : "bg-[#09090b] border-[#27272a] hover:bg-[#18181b] hover:border-[#3f3f46]"
                                                        )}
                                                    >
                                                        <div className={cn(
                                                            "size-8 rounded flex items-center justify-center border",
                                                            source.type === 'smb' ? "bg-blue-500/10 border-blue-500/20 text-blue-400" :
                                                                source.type === 's3' ? "bg-orange-500/10 border-orange-500/20 text-orange-400" :
                                                                    "bg-[#27272a] border-[#3f3f46] text-[#71717a]"
                                                        )}>
                                                            <span className="material-symbols-outlined text-lg">
                                                                {source.type === 'smb' ? 'folder_shared' : source.type === 's3' ? 'cloud' : 'storage'}
                                                            </span>
                                                        </div>
                                                        <div>
                                                            <div className="text-xs font-bold text-white mb-0.5">{source.name}</div>
                                                            <div className="text-[10px] text-[#71717a] font-mono truncate max-w-[200px] flex items-center gap-1">
                                                                {source.config.path || source.config.bucket || 'No path configured'}
                                                            </div>
                                                        </div>
                                                        {selectedSourceId === source.id && (
                                                            <span className="material-symbols-outlined text-primary text-xl ml-auto animate-in zoom-in spin-in-180 duration-300">check_circle</span>
                                                        )}
                                                    </button>
                                                ))}
                                            </div>
                                        ) : (
                                            <div className="text-center py-10 border border-dashed border-[#27272a] rounded bg-[#09090b]">
                                                <span className="material-symbols-outlined text-4xl text-[#71717a] mb-2">cloud_off</span>
                                                <p className="text-xs text-[#71717a] mb-4">No managed sources configured</p>
                                                <button onClick={() => setSourceTab('manual')} className="text-[10px] text-primary hover:underline uppercase font-bold tracking-widest">
                                                    Enter path manually
                                                </button>
                                            </div>
                                        )
                                    ) : sourceTab === 'manual' ? (
                                        <div className="space-y-4">
                                            <div className="bg-[#09090b] border border-[#27272a] rounded p-4">
                                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest block mb-2">Local or Network Path</label>
                                                <div className="flex gap-2">
                                                    <span className="flex items-center justify-center size-10 rounded bg-[#27272a] text-[#71717a] border border-[#3f3f46]">
                                                        <span className="material-symbols-outlined">folder_open</span>
                                                    </span>
                                                    <input
                                                        type="text"
                                                        value={sourcePath}
                                                        onChange={(e) => {
                                                            setSourcePath(e.target.value);
                                                            setSelectedSourceId(null);
                                                        }}
                                                        className="flex-1 bg-black/40 border border-[#27272a] rounded px-4 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all placeholder:text-[#3f3f46]"
                                                        placeholder="//server/share or /local/path"
                                                    />
                                                </div>
                                                <p className="text-[10px] text-[#71717a] mt-2 pl-1">
                                                    Enter the full path to the directory you want to back up.
                                                    <br />For network shares, ensure the path is mounted or accessible.
                                                </p>
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            <BrowserUploadStep
                                                groupId={uploadGroupId}
                                                onUploadComplete={(paths) => {
                                                    // Set sourcePath to the directory containing uploads
                                                    if (paths.length > 0) {
                                                        // paths[0] is like .../ready/<groupId>/filename
                                                        // We want .../ready/<groupId>
                                                        // Simple split/join safely
                                                        const p = paths[0];
                                                        const idx = p.lastIndexOf('/');
                                                        const dir = idx > 0 ? p.substring(0, idx) : p;
                                                        setSourcePath(dir);
                                                    }
                                                }}
                                            />
                                            {sourcePath && (
                                                <div className="flex items-center gap-2 p-3 bg-green-500/10 border border-green-500/20 rounded-md">
                                                    <span className="material-symbols-outlined text-green-500">check_circle</span>
                                                    <div>
                                                        <p className="text-xs font-bold text-green-500">Uploads Staged Successfully</p>
                                                        <p className="text-[10px] text-green-400/80 font-mono break-all">{sourcePath}</p>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Step 3: Destination Selection */}
                    {step === 'destination' && (
                        <div className="space-y-4">
                            <div className="text-center mb-6">
                                <h3 className="text-lg font-semibold text-white">
                                    {jobType === 'restore' ? 'Select Destination Folder' : 'Select Destination Tape'}
                                </h3>
                                <p className="text-xs text-[#71717a] mt-1">
                                    {jobType === 'restore'
                                        ? 'Choose where to restore the files'
                                        : 'Choose the target tape for this operation'}
                                </p>
                            </div>

                            {jobType === 'restore' ? (
                                <div className="space-y-4">
                                    <div className="bg-[#09090b] border border-[#27272a] rounded p-4 space-y-4">
                                        <div className="space-y-2">
                                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest block px-1">Internal Restore Folder (Recommended)</label>
                                            <button
                                                onClick={() => setSourcePath('/var/lib/fossilsafe/restore')}
                                                className={cn(
                                                    "w-full flex items-center gap-3 p-3 rounded border text-left transition-all",
                                                    sourcePath === '/var/lib/fossilsafe/restore'
                                                        ? "bg-primary/10 border-primary/50 text-white"
                                                        : "bg-black/40 border-[#27272a] text-[#71717a] hover:bg-[#18181b]"
                                                )}
                                            >
                                                <span className="material-symbols-outlined text-primary">folder_special</span>
                                                <div className="flex-1">
                                                    <div className="text-xs font-bold uppercase tracking-wider">Fast Internal Storage</div>
                                                    <div className="text-[10px] font-mono opacity-60">/var/lib/fossilsafe/restore</div>
                                                </div>
                                                {sourcePath === '/var/lib/fossilsafe/restore' && (
                                                    <span className="material-symbols-outlined text-primary">check_circle</span>
                                                )}
                                            </button>
                                        </div>

                                        <div className="space-y-2 pt-2 border-t border-[#27272a]">
                                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest block px-1">Custom Absolute Path</label>
                                            <div className="flex gap-2">
                                                <input
                                                    type="text"
                                                    value={sourcePath === '/var/lib/fossilsafe/restore' ? '' : sourcePath}
                                                    onChange={(e) => {
                                                        setSourcePath(e.target.value);
                                                        setSelectedSourceId(null);
                                                    }}
                                                    className="flex-1 bg-black/40 border border-[#27272a] rounded px-4 py-2.5 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all placeholder:text-[#3f3f46]"
                                                    placeholder="/mnt/restores/my_data"
                                                />
                                            </div>
                                            <p className="text-[10px] text-[#71717a] mt-1 pr-1 italic">
                                                Ensure the appliance has write permissions to this directory.
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Target Tape</label>
                                    <TapeSelect
                                        tapes={tapes}
                                        selectedBarcode={selectedTape}
                                        onSelect={(barcode) => setSelectedTape(barcode)}
                                        filter={(t) => t.status === 'available' || t.status === 'in_use' || t.status === 'full'}
                                        className="h-[320px]"
                                    />

                                    {/* Real-time Capacity Feedback */}
                                    {(estimatedSize !== null && selectedTape) && (
                                        <div className={cn(
                                            "mt-4 p-3 rounded-md border flex items-start gap-3 animate-in fade-in slide-in-from-top-2 duration-300",
                                            capacityResult?.sufficient
                                                ? "bg-primary/10 border-primary/20"
                                                : "bg-destructive/10 border-destructive/20"
                                        )}>
                                            <span className={cn(
                                                "material-symbols-outlined mt-0.5",
                                                capacityResult?.sufficient ? "text-primary" : "text-destructive"
                                            )}>
                                                {isValidatingCapacity ? 'sync' : (capacityResult?.sufficient ? 'check_circle' : 'warning')}
                                            </span>
                                            <div className="flex-1">
                                                <div className="flex items-center justify-between">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-white">Capacity Validation</span>
                                                    {isValidatingCapacity && <span className="text-[8px] animate-pulse text-primary font-mono">CALCULATING...</span>}
                                                </div>
                                                <div className="text-[10px] text-[#71717a] mt-1">
                                                    Source: <span className="font-mono text-[#a1a1aa]">{format_bytes(estimatedSize)}</span>
                                                    <span className="mx-2 opacity-30">|</span>
                                                    Selectable: <span className="font-mono text-[#a1a1aa]">{format_bytes(capacityResult?.total_available || 0)}</span>
                                                </div>
                                                {!isValidatingCapacity && capacityResult && !capacityResult.sufficient && (
                                                    <div className="text-[10px] text-destructive font-bold mt-1.5 flex items-center gap-1">
                                                        <span className="material-symbols-outlined text-[12px]">error</span>
                                                        INSUFFICIENT SPACE: Need {format_bytes(capacityResult.shortage)} more.
                                                    </div>
                                                )}
                                                {!isValidatingCapacity && capacityResult?.sufficient && (
                                                    <div className="text-[10px] text-primary font-bold mt-1.5 flex items-center gap-1">
                                                        <span className="material-symbols-outlined text-[12px]">verified</span>
                                                        READY: Sufficient capacity on selected media.
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Step: Consistency & Hooks */}
                    {step === 'hooks' && (
                        <div className="space-y-6 animate-in fade-in slide-in-from-right-4 duration-300">
                                <div className="text-center mb-6">
                                    <h3 className="text-lg font-semibold text-white">Consistency Hooks</h3>
                                    <p className="text-xs text-[#71717a] mt-1">Configure pre- and post-execution scripts for data consistency</p>
                                </div>

                                <div className="p-4 bg-primary/5 border border-primary/20 rounded-lg flex gap-4 items-start">
                                    <span className="material-symbols-outlined text-primary text-xl">security</span>
                                    <div className="space-y-1">
                                        <div className="text-[10px] font-bold text-primary uppercase tracking-widest">Consistency Warning</div>
                                        <p className="text-[10px] text-primary/70 leading-relaxed font-medium">
                                            Hooks run with appliance privileges. Ensure scripts in <code className="bg-primary/10 px-1 rounded">/etc/fossilsafe/hooks.d/</code> are secure and tested.
                                            Pre-hooks can abort the job if they exit with a non-zero code.
                                        </p>
                                    </div>
                                </div>

                                <div className="grid grid-cols-1 gap-6">
                                    <div className="space-y-3">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Pre-Execution Hook (Consistency Check/Quiesce)</label>
                                        <select
                                            value={preHook}
                                            onChange={(e) => setPreHook(e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-2.5 text-sm text-white focus:border-primary/50 outline-none transition-all appearance-none cursor-pointer"
                                        >
                                            <option value="">None (Standard Backup)</option>
                                            {availableHooks.pre.map(hook => (
                                                <option key={hook.id} value={hook.id}>{hook.name} ({hook.id})</option>
                                            ))}
                                        </select>
                                        <p className="text-[9px] text-[#71717a] px-1 italic">Runs before backup starts. Use for database dumping or FS snapshots.</p>
                                    </div>

                                    <div className="space-y-3">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Post-Execution Hook (Cleanup/Restart)</label>
                                        <select
                                            value={postHook}
                                            onChange={(e) => setPostHook(e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-2.5 text-sm text-white focus:border-primary/50 outline-none transition-all appearance-none cursor-pointer"
                                        >
                                            <option value="">None (Standard Cleanup)</option>
                                            {availableHooks.post.map(hook => (
                                                <option key={hook.id} value={hook.id}>{hook.name} ({hook.id})</option>
                                            ))}
                                        </select>
                                        <p className="text-[9px] text-[#71717a] px-1 italic">Runs after backup completes. Use for restarting services or cleanup.</p>
                                    </div>
                                </div>
                        </div>
                    )}

                    {/* Step: Preflight & Spanning Preview */}
                    {step === 'preflight' && (
                        <div className="space-y-6 animate-in fade-in slide-in-from-right-4 duration-300">
                            <div className="text-center mb-6">
                                <h3 className="text-lg font-semibold text-white">Preflight Inspection</h3>
                                <p className="text-xs text-[#71717a] mt-1">Verification of hardware readiness and data distribution</p>
                            </div>

                            {isPreflighting ? (
                                <div className="py-20 flex flex-col items-center justify-center space-y-4">
                                    <div className="size-12 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
                                    <p className="text-xs font-mono text-primary animate-pulse">ALGORITHMIC SCAN IN PROGRESS...</p>
                                </div>
                            ) : preflightResults && (
                                <div className="space-y-6">
                                    {/* Tape Readiness */}
                                    <div className="space-y-3">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Hardware Readiness</label>
                                        <div className="grid grid-cols-1 gap-2">
                                            <div className="flex items-center gap-3 p-3 bg-black/40 border border-[#27272a] rounded">
                                                <span className={cn(
                                                    "material-symbols-outlined text-lg",
                                                    preflightResults.result?.estimates?.sufficient_tapes ? "text-primary" : "text-destructive"
                                                )}>
                                                    {preflightResults.result?.estimates?.sufficient_tapes ? 'check_circle' : 'warning'}
                                                </span>
                                                <div className="flex-1">
                                                    <div className="text-xs font-bold text-white uppercase tracking-wider">Tape Media</div>
                                                    <div className="text-[10px] text-[#71717a] font-medium">
                                                        {preflightResults.result?.estimates?.sufficient_tapes
                                                            ? `Sufficient media available (${preflightResults.result?.estimates?.available_tapes} tapes)`
                                                            : `Insufficient media: Need ${preflightResults.result?.estimates?.tapes_needed_compressed}, have ${preflightResults.result?.estimates?.available_tapes}`
                                                        }
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Spanning Forecast */}
                                    <div className="space-y-3">
                                        <div className="flex items-center justify-between px-1">
                                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Spanning Forecast</label>
                                            <span className="text-[9px] font-mono text-primary uppercase">Estimated Layout</span>
                                        </div>
                                        <div className="bg-[#09090b] border border-[#27272a] rounded-lg p-4 space-y-4">
                                                <div className="flex items-center gap-6">
                                                    <div className="flex flex-col">
                                                        <span className="text-[8px] font-bold text-[#71717a] uppercase tracking-tight">Total Est. Size</span>
                                                        <span className="text-sm font-mono text-white font-bold">{format_bytes(preflightResults.result?.estimates?.total_bytes)}</span>
                                                    </div>
                                                    <div className="h-8 w-px bg-[#27272a]"></div>
                                                    <div className="flex flex-col">
                                                        <span className="text-sm font-mono text-white font-bold">{preflightResults.result?.estimates?.tapes_needed_compressed} LTO-8 Tapes</span>
                                                    </div>
                                                </div>

                                                {/* Visual Tape Spanning Bar */}
                                                <div className="space-y-2">
                                                    <div className="flex justify-between text-[8px] font-mono text-[#71717a]">
                                                        <span>MEDIA_SPANNING_FORECAST</span>
                                                        <span>{preflightResults.result?.estimates?.tapes_needed_compressed} SEGMENTS</span>
                                                    </div>
                                                    <div className="flex gap-1 h-2">
                                                        {Array.from({ length: preflightResults.result?.estimates?.tapes_needed_compressed || 1 }).map((_, i) => (
                                                            <div
                                                                key={i}
                                                                className="flex-1 bg-primary/40 rounded-sm border border-primary/20 shadow-[0_0_5px_rgba(25,230,100,0.2)] animate-pulse"
                                                                style={{ animationDelay: `${i * 150}ms` }}
                                                            />
                                                        ))}
                                                    </div>
                                                </div>
                                            </div>
                                        <p className="text-[9px] text-[#71717a] italic mt-2 border-t border-[#27272a] pt-2">
                                            * Estimates based on 2.5:1 hardware-assisted compression. Actual utilization depends on data entropy.
                                        </p>
                                    </div>

                                    {/* Resolve Tape Initialization */}
                                    {tapes.find(t => t.barcode === selectedTape && !t.ltfs_formatted) && (
                                        <div className="p-3 bg-orange-500/10 border border-orange-500/20 rounded-md flex items-center justify-between">
                                            <div className="flex items-center gap-2">
                                                <span className="material-symbols-outlined text-orange-400">info</span>
                                                <p className="text-[10px] font-bold text-orange-400 uppercase tracking-widest">Selected tape requires initialization</p>
                                            </div>
                                            <button
                                                onClick={() => handleFormatTape(selectedTape)}
                                                className="px-3 py-1 bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 text-[10px] font-bold uppercase rounded border border-orange-500/30"
                                            >
                                                Initialize Now
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Step 4: Confirmation */}
                    {step === 'confirm' && (
                        <div className="space-y-6">
                            <div className="text-center mb-4">
                                <h3 className="text-lg font-semibold text-white">Review & Confirm</h3>
                                <p className="text-xs text-[#71717a] mt-1">Verify the job details before creating</p>
                            </div>

                            <div className="bg-[#09090b] border border-[#27272a] rounded-lg p-4 space-y-3">
                                <div className="flex justify-between items-center py-2 border-b border-[#27272a]">
                                    <span className="text-[10px] text-[#71717a] uppercase tracking-wider">Job Type</span>
                                    <span className="text-sm font-mono text-primary uppercase font-bold">{jobType}</span>
                                </div>
                                <div className="flex justify-between items-center py-2 border-b border-[#27272a]">
                                    <span className="text-[10px] text-[#71717a] uppercase tracking-wider">Source</span>
                                    <span className="text-xs font-mono text-white truncate max-w-[200px]">{sourcePath}</span>
                                </div>
                                <div className="flex justify-between items-center py-2">
                                    <span className="text-[10px] text-[#71717a] uppercase tracking-wider">Target Tape</span>
                                    <span className="text-sm font-mono text-white">{selectedTape}</span>
                                </div>
                            </div>

                            {/* Advanced Options */}
                            <div className="grid grid-cols-2 gap-4">
                                <div className={cn(
                                    "flex items-center justify-between p-3 rounded border transition-all",
                                    compression ? "bg-primary/5 border-primary/30" : "bg-black/40 border-[#27272a]"
                                )}>
                                    <div className="flex items-center gap-2">
                                        <span className="material-symbols-outlined text-[16px] text-[#71717a]">compress</span>
                                        <span className="text-[10px] font-bold text-white uppercase tracking-wider">Compression</span>
                                    </div>
                                    <input type="checkbox" checked={compression} onChange={e => setCompression(e.target.checked)} className="accent-primary" />
                                </div>
                                <div className="space-y-2">
                                    <div className="text-[10px] font-bold text-[#71717a] uppercase tracking-wider px-1">Encryption Mode</div>
                                    <div className="grid grid-cols-3 gap-2">
                                        {[
                                            { id: 'none', label: 'None', icon: 'lock_open' },
                                            { id: 'software', label: 'Software', icon: 'enhanced_encryption' },
                                            { id: 'hardware', label: 'Hardware', icon: 'key' }
                                        ].map((type) => (
                                            <button
                                                key={type.id}
                                                type="button"
                                                onClick={() => setEncryption(type.id as any)}
                                                className={cn(
                                                    "flex flex-col items-center justify-center p-2 rounded border transition-all gap-1",
                                                    encryption === type.id
                                                        ? "bg-primary/10 border-primary/40 text-primary"
                                                        : "bg-black/40 border-[#27272a] text-[#71717a] hover:border-[#3f3f46]"
                                                )}
                                            >
                                                <span className="material-symbols-outlined text-lg">{type.icon}</span>
                                                <span className="text-[9px] font-bold uppercase tracking-tighter">{type.label}</span>
                                            </button>
                                        ))}
                                    </div>
                                    </div>
                            </div>

                            {/* Zero-Knowledge Passphrase Input */}
                            {encryption === 'software' && (
                                <div className="space-y-3 p-4 bg-orange-500/5 border border-orange-500/20 rounded-lg animate-in fade-in slide-in-from-top-2">
                                    <div className="flex items-center gap-2">
                                        <span className="material-symbols-outlined text-orange-400 text-sm">enhanced_encryption</span>
                                        <label className="text-[10px] font-bold text-orange-400 uppercase tracking-widest">Zero-Knowledge Passphrase</label>
                                    </div>
                                    <input
                                        type="password"
                                        value={encryptionPassword}
                                        onChange={(e) => setEncryptionPassword(e.target.value)}
                                        placeholder="Enter strong encryption passphrase..."
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-2.5 text-xs font-mono text-white focus:border-orange-500/50 outline-none transition-all"
                                    />
                                    <div className="p-2.5 bg-destructive/10 border border-destructive/20 rounded flex gap-2">
                                        <span className="material-symbols-outlined text-destructive text-sm leading-none">warning</span>
                                        <p className="text-[9px] text-destructive/80 font-bold leading-normal uppercase">
                                            CRITICAL: FossilSafe does NOT store this passphrase. If lost, data recovery is impossible.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* Encryption Information Info */}
                            {encryption !== 'none' && encryption !== 'software' && (
                                <div className="p-3 bg-blue-500/10 border border-blue-500/20 rounded-lg flex gap-3">
                                    <span className="material-symbols-outlined text-blue-400 text-lg">
                                        memory
                                    </span>
                                    <div>
                                        <div className="text-[10px] font-bold text-blue-400 uppercase tracking-widest mb-1">
                                            Hardware Encryption (LTO native)
                                        </div>
                                        <p className="text-[10px] text-blue-400/80 leading-relaxed font-medium">
                                            Enables native LTO drive encryption. Keys are sent securely to the drive hardware.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* Drive Busy Warning */}
                            {driveBusy && (
                                <div className="p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg flex gap-3 animate-in fade-in slide-in-from-bottom-2">
                                    <span className="material-symbols-outlined text-yellow-500 text-lg">schedule</span>
                                    <div>
                                        <div className="text-[10px] font-bold text-yellow-500 uppercase tracking-widest mb-1">Queue Warning</div>
                                        <p className="text-[10px] text-yellow-500/80 leading-relaxed font-medium">
                                            The tape drive is currently busy. This job will be queued and start automatically when the drive becomes available.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* Archive Warning */}
                            {jobType === 'archive' && (
                                <div className="p-4 bg-destructive/5 border border-destructive/20 rounded-lg">
                                    <label className="flex items-start gap-4 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={confirmArchive}
                                            onChange={(e) => setConfirmArchive(e.target.checked)}
                                            className="mt-1 accent-destructive"
                                        />
                                        <div>
                                            <div className="text-[10px] font-bold text-destructive uppercase tracking-widest mb-1">Destructive Confirmation</div>
                                            <p className="text-[10px] text-destructive/70 leading-relaxed font-medium">
                                                I understand that all source files will be **deleted** from disk after the archive is verified on tape.
                                            </p>
                                        </div>
                                    </label>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="flex items-center justify-between px-6 py-4 border-t border-[#27272a] bg-[#09090b]">
                    <div className="flex items-center gap-2">
                        <button onClick={handleClose} className="px-4 py-2 text-[#71717a] hover:text-white text-xs font-bold uppercase tracking-wider">
                            Cancel
                        </button>
                        {step === 'destination' && selectedTape && (
                            // Inline Format Option if tape is blank/unformatted
                            // Checking if tape needs formatting (simplified check)
                            tapes.find(t => t.barcode === selectedTape && !t.ltfs_formatted) && (
                                <button
                                    onClick={() => handleFormatTape(selectedTape)}
                                    className="text-[10px] text-orange-400 hover:text-orange-300 font-bold uppercase tracking-widest flex items-center gap-1"
                                >
                                    <span className="material-symbols-outlined text-sm">format_shapes</span>
                                    Initialize Tape
                                </button>
                            )
                        )}
                    </div>
                    <div className="flex gap-2">
                        {currentStepIndex > 0 && (
                            <button onClick={() => handleChangeStep(-1)} className="px-4 py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white text-xs font-bold uppercase rounded">
                                Back
                            </button>
                        )}
                        <button
                            onClick={step === 'confirm' ? handleCreateJob : handleNextStep}
                            disabled={!canProceed() || isLoading || isPreflighting}
                            className={cn(
                                "px-6 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase rounded shadow-glow transition-all",
                                (!canProceed() || isLoading || isPreflighting) && "opacity-50 cursor-not-allowed"
                            )}
                        >
                            {step === 'confirm' ? (isLoading ? 'Creating...' : 'Create Job') : (isPreflighting ? 'Preflighting...' : 'Next')}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}
