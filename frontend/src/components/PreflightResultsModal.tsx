import { X, CheckCircle2, AlertTriangle, XCircle, Info } from 'lucide-react'
import { cn } from '@/lib/utils'

interface PreflightCheck {
    name: string
    status: 'pass' | 'warning' | 'fail'
    message: string
    details?: string
}

interface PreflightResultsModalProps {
    isOpen: boolean
    onClose: () => void
    onProceed: () => void
    results: PreflightCheck[]
    isLoading?: boolean
}

export default function PreflightResultsModal({
    isOpen,
    onClose,
    onProceed,
    results,
    isLoading = false
}: PreflightResultsModalProps) {
    if (!isOpen) return null

    const hasFailures = results.some(r => r.status === 'fail')
    const hasWarnings = results.some(r => r.status === 'warning')
    const allPassed = results.every(r => r.status === 'pass')

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'pass':
                return <CheckCircle2 className="size-4 text-primary" />
            case 'warning':
                return <AlertTriangle className="size-4 text-amber-500" />
            case 'fail':
                return <XCircle className="size-4 text-destructive" />
            default:
                return <Info className="size-4 text-[#71717a]" />
        }
    }

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/80 backdrop-blur-sm animate-in fade-in"
                onClick={onClose}
            />

            {/* Modal */}
            <div className="relative w-full max-w-2xl bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
                {/* Header */}
                <div className="px-6 py-4 border-b border-[#27272a] flex items-center justify-between bg-[#18181b]/50">
                    <div className="flex items-center gap-3">
                        <div className={cn(
                            "size-10 rounded-lg flex items-center justify-center",
                            allPassed && "bg-primary/10",
                            hasWarnings && !hasFailures && "bg-amber-500/10",
                            hasFailures && "bg-destructive/10"
                        )}>
                            {isLoading && (
                                <span className="material-symbols-outlined text-xl text-primary animate-spin">sync</span>
                            )}
                            {!isLoading && allPassed && (
                                <CheckCircle2 className="size-5 text-primary" />
                            )}
                            {!isLoading && hasWarnings && !hasFailures && (
                                <AlertTriangle className="size-5 text-amber-500" />
                            )}
                            {!isLoading && hasFailures && (
                                <XCircle className="size-5 text-destructive" />
                            )}
                        </div>
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">
                                {isLoading ? 'Running Preflight Checks' : 'Preflight Results'}
                            </h2>
                            <p className="text-[10px] text-[#71717a] font-mono uppercase">
                                {isLoading ? 'Validating Configuration' : `${results.length} Checks Completed`}
                            </p>
                        </div>
                    </div>
                    <button onClick={onClose} className="text-[#71717a] hover:text-white transition-colors">
                        <X className="size-5" />
                    </button>
                </div>

                {/* Results */}
                <div className="p-6 max-h-[500px] overflow-y-auto">
                    {isLoading ? (
                        <div className="flex flex-col items-center justify-center py-12 gap-4">
                            <div className="size-12 rounded-full border-2 border-primary/20 border-t-primary animate-spin"></div>
                            <p className="text-xs text-[#71717a]">Validating job configuration...</p>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {results.map((check, index) => (
                                <div
                                    key={index}
                                    className={cn(
                                        "p-4 rounded border transition-all",
                                        check.status === 'pass' && "bg-primary/5 border-primary/30",
                                        check.status === 'warning' && "bg-amber-500/5 border-amber-500/30",
                                        check.status === 'fail' && "bg-destructive/5 border-destructive/30"
                                    )}
                                >
                                    <div className="flex items-start gap-3">
                                        <div className="mt-0.5">
                                            {getStatusIcon(check.status)}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center justify-between gap-2">
                                                <h4 className="text-xs font-bold text-white uppercase tracking-wider">
                                                    {check.name}
                                                </h4>
                                                <span className={cn(
                                                    "px-2 py-0.5 rounded text-[8px] font-bold uppercase tracking-widest",
                                                    check.status === 'pass' && "bg-primary/20 text-primary",
                                                    check.status === 'warning' && "bg-amber-500/20 text-amber-500",
                                                    check.status === 'fail' && "bg-destructive/20 text-destructive"
                                                )}>
                                                    {check.status}
                                                </span>
                                            </div>
                                            <p className="text-[10px] text-[#71717a] mt-1 leading-relaxed">
                                                {check.message}
                                            </p>
                                            {check.details && (
                                                <div className="mt-2 p-2 rounded bg-black/40 border border-[#27272a]">
                                                    <p className="text-[9px] text-[#71717a] font-mono">{check.details}</p>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Footer */}
                {!isLoading && (
                    <div className="px-6 py-4 border-t border-[#27272a] bg-[#18181b]/30 flex items-center justify-between gap-4">
                        <div className="flex-1">
                            {hasFailures && (
                                <div className="flex items-center gap-2 text-destructive">
                                    <XCircle className="size-4" />
                                    <p className="text-[10px] font-bold uppercase tracking-wider">
                                        Critical issues detected - cannot proceed
                                    </p>
                                </div>
                            )}
                            {hasWarnings && !hasFailures && (
                                <div className="flex items-center gap-2 text-amber-500">
                                    <AlertTriangle className="size-4" />
                                    <p className="text-[10px] font-bold uppercase tracking-wider">
                                        Warnings detected - proceed with caution
                                    </p>
                                </div>
                            )}
                            {allPassed && (
                                <div className="flex items-center gap-2 text-primary">
                                    <CheckCircle2 className="size-4" />
                                    <p className="text-[10px] font-bold uppercase tracking-wider">
                                        All checks passed - ready to proceed
                                    </p>
                                </div>
                            )}
                        </div>

                        <div className="flex gap-3">
                            <button
                                onClick={onClose}
                                className="px-4 py-2 rounded border border-[#27272a] hover:bg-[#27272a] text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-wider transition-all"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={onProceed}
                                disabled={hasFailures}
                                className={cn(
                                    "px-4 py-2 rounded text-[10px] font-bold uppercase tracking-wider transition-all",
                                    hasFailures && "bg-[#27272a] text-[#71717a] cursor-not-allowed",
                                    !hasFailures && "bg-primary hover:bg-green-400 text-black"
                                )}
                            >
                                {hasWarnings ? 'Proceed Anyway' : 'Create Job'}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
