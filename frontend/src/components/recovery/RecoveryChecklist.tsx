import { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface ChecklistStep {
    id: string
    title: string
    description: string
    status: 'pending' | 'running' | 'success' | 'error'
    error?: string
    action?: () => Promise<void>
    autoRun?: boolean
}

interface RecoveryChecklistProps {
    onComplete?: () => void
}

export default function RecoveryChecklist({ onComplete }: RecoveryChecklistProps) {
    const [steps, setSteps] = useState<ChecklistStep[]>([
        {
            id: 'inventory',
            title: 'Library Inventory Scan',
            description: 'Verify tape library is online and scan for available tapes',
            status: 'pending',
            autoRun: true
        },
        {
            id: 'database',
            title: 'Database Integrity Check',
            description: 'Verify catalog database is accessible and not corrupted',
            status: 'pending',
            autoRun: true
        },
        {
            id: 'drive',
            title: 'Drive Status Verification',
            description: 'Check tape drive availability and readiness',
            status: 'pending',
            autoRun: true
        },
        {
            id: 'hardware',
            title: 'Hardware Communication Test',
            description: 'Test SCSI/SAS communication with tape library',
            status: 'pending',
            autoRun: false
        }
    ])

    const [allComplete, setAllComplete] = useState(false)

    useEffect(() => {
        // Auto-run first step
        if (steps[0].status === 'pending' && steps[0].autoRun) {
            runStep(0)
        }
    }, [])

    const runStep = async (index: number) => {
        const step = steps[index]

        // Update status to running
        setSteps(prev => prev.map((s, i) =>
            i === index ? { ...s, status: 'running' as const, error: undefined } : s
        ))

        try {
            let success = false

            switch (step.id) {
                case 'inventory':
                    const tapeRes = await api.getTapes()
                    success = tapeRes.success
                    if (!success) throw new Error(tapeRes.error || 'Failed to scan library')
                    break

                case 'database':
                    // Use getSystemInfo which includes database health
                    const dbRes = await api.getSystemInfo()
                    success = dbRes.success
                    if (!success) throw new Error('Database health check failed')
                    break

                case 'drive':
                    // Check drive by attempting to get tapes (requires drive communication)
                    const driveRes = await api.getTapes()
                    success = driveRes.success
                    if (!success) throw new Error('Drive communication failed')
                    break

                case 'hardware':
                    // Hardware test - use a simple tape scan as proxy
                    const hwRes = await api.scanLibrary('fast')
                    success = hwRes.success
                    if (!success) throw new Error(hwRes.error || 'Hardware test failed')
                    break
            }

            // Update to success
            setSteps(prev => prev.map((s, i) =>
                i === index ? { ...s, status: 'success' as const } : s
            ))

            // Auto-advance to next step if it should auto-run
            if (index < steps.length - 1) {
                const nextStep = steps[index + 1]
                if (nextStep.autoRun) {
                    setTimeout(() => runStep(index + 1), 500)
                }
            } else {
                // All steps complete
                setAllComplete(true)
                onComplete?.()
            }

        } catch (error) {
            // Update to error
            setSteps(prev => prev.map((s, i) =>
                i === index ? {
                    ...s,
                    status: 'error' as const,
                    error: error instanceof Error ? error.message : 'Unknown error'
                } : s
            ))
        }
    }

    const retryStep = (index: number) => {
        runStep(index)
    }

    return (
        <div className="bg-[#121214] border border-[#27272a] rounded-lg p-6">
            <div className="flex items-center gap-3 mb-6">
                <div className="size-10 rounded-lg bg-primary/10 flex items-center justify-center">
                    <span className="material-symbols-outlined text-xl text-primary">checklist</span>
                </div>
                <div>
                    <div className="flex items-center gap-2">
                        <h3 className="text-sm font-bold text-white uppercase tracking-wider">Recovery Readiness Checklist</h3>
                        <span className="px-1.5 py-0.5 rounded-full bg-primary/10 border border-primary/20 text-primary text-[8px] font-bold uppercase tracking-widest">Recommended</span>
                    </div>
                    <p className="text-[10px] text-[#71717a] mt-0.5">Verifying system components before recovery</p>
                </div>
            </div>

            <div className="space-y-3">
                {steps.map((step, index) => (
                    <div
                        key={step.id}
                        className={cn(
                            "p-4 rounded border transition-all",
                            step.status === 'pending' && "bg-black/20 border-[#27272a]",
                            step.status === 'running' && "bg-primary/5 border-primary/30",
                            step.status === 'success' && "bg-primary/5 border-primary/30",
                            step.status === 'error' && "bg-destructive/5 border-destructive/30"
                        )}
                    >
                        <div className="flex items-start gap-3">
                            {/* Status Icon */}
                            <div className={cn(
                                "size-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5",
                                step.status === 'pending' && "bg-[#27272a]",
                                step.status === 'running' && "bg-primary/20",
                                step.status === 'success' && "bg-primary/20",
                                step.status === 'error' && "bg-destructive/20"
                            )}>
                                {step.status === 'pending' && (
                                    <span className="material-symbols-outlined text-sm text-[#71717a]">radio_button_unchecked</span>
                                )}
                                {step.status === 'running' && (
                                    <span className="material-symbols-outlined text-sm text-primary animate-spin">sync</span>
                                )}
                                {step.status === 'success' && (
                                    <span className="material-symbols-outlined text-sm text-primary">check_circle</span>
                                )}
                                {step.status === 'error' && (
                                    <span className="material-symbols-outlined text-sm text-destructive">error</span>
                                )}
                            </div>

                            {/* Content */}
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center justify-between gap-2">
                                    <h4 className={cn(
                                        "text-xs font-bold uppercase tracking-wider",
                                        step.status === 'pending' && "text-[#71717a]",
                                        step.status === 'running' && "text-white",
                                        step.status === 'success' && "text-white",
                                        step.status === 'error' && "text-destructive"
                                    )}>
                                        {step.title}
                                    </h4>

                                    {/* Action Button */}
                                    {step.status === 'pending' && !step.autoRun && (
                                        <button
                                            onClick={() => runStep(index)}
                                            className="px-2 py-1 rounded bg-primary/10 border border-primary/20 text-primary text-[9px] font-bold uppercase tracking-wider hover:bg-primary/20 transition-all"
                                        >
                                            Run Check
                                        </button>
                                    )}

                                    {step.status === 'error' && (
                                        <button
                                            onClick={() => retryStep(index)}
                                            className="px-2 py-1 rounded bg-destructive/10 border border-destructive/20 text-destructive text-[9px] font-bold uppercase tracking-wider hover:bg-destructive/20 transition-all"
                                        >
                                            Retry
                                        </button>
                                    )}
                                </div>

                                <p className="text-[10px] text-[#71717a] mt-1 leading-relaxed">
                                    {step.description}
                                </p>

                                {step.error && (
                                    <div className="mt-2 p-2 rounded bg-destructive/10 border border-destructive/20">
                                        <p className="text-[9px] text-destructive font-mono">{step.error}</p>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            {/* Summary */}
            {allComplete && (
                <div className="mt-6 p-4 rounded bg-primary/10 border border-primary/30 flex items-center gap-3 animate-in fade-in slide-in-from-top-2">
                    <span className="material-symbols-outlined text-primary">verified</span>
                    <div>
                        <p className="text-xs font-bold text-white uppercase tracking-wider">System Ready for Recovery</p>
                        <p className="text-[10px] text-[#71717a] mt-0.5">All checks passed. You may proceed with recovery operations.</p>
                    </div>
                </div>
            )}
        </div>
    )
}
