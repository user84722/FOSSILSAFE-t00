import { useState } from "react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useDemoMode } from "@/lib/demoMode"

interface CatalogExportModalProps {
    isOpen: boolean
    onClose: () => void
    tapes: Array<{ barcode: string; alias?: string; status: string }>
}

export default function CatalogExportModal({ isOpen, onClose, tapes }: CatalogExportModalProps) {
    const { isDemoMode } = useDemoMode()
    const [selectedTape, setSelectedTape] = useState('')
    const [isExporting, setIsExporting] = useState(false)
    const [result, setResult] = useState<{ success: boolean; message: string } | null>(null)

    const handleExport = async () => {
        if (!selectedTape) return
        setIsExporting(true)
        setResult(null)

        if (isDemoMode) {
            setTimeout(() => {
                setIsExporting(false)
                setResult({ success: true, message: 'Catalog exported successfully to tape ' + selectedTape })
            }, 1500)
        } else {
            try {
                const res = await api.exportCatalog(selectedTape)
                if (res.success) {
                    setResult({ success: true, message: res.data?.message || 'Export successful' })
                } else {
                    setResult({ success: false, message: res.error || 'Export failed' })
                }
            } catch (err) {
                setResult({ success: false, message: 'Network error' })
            } finally {
                setIsExporting(false)
            }
        }
    }

    const reset = () => {
        setSelectedTape('')
        setResult(null)
        onClose()
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={reset} />
            <div className="relative w-full max-w-md bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl p-6">
                <div className="flex justify-between items-center mb-6">
                    <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                        <span className="material-symbols-outlined text-primary">save</span>
                        Export Catalog
                    </h2>
                    <button onClick={reset} className="text-[#71717a] hover:text-white">
                        <span className="material-symbols-outlined">close</span>
                    </button>
                </div>

                {!result ? (
                    <div className="space-y-4">
                        <p className="text-xs text-[#71717a]">
                            Exporting the catalog metadata allows you to restore the database structure on a standard FossilSafe instance without rescanning all tapes.
                        </p>

                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Target Tape</label>
                            <select
                                value={selectedTape}
                                onChange={(e) => setSelectedTape(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                            >
                                <option value="">Select a tape...</option>
                                {tapes.filter(t => t.status === 'available' || t.status === 'Online').map(t => (
                                    <option key={t.barcode} value={t.barcode}>{t.barcode} {t.alias ? `(${t.alias})` : ''}</option>
                                ))}
                                {isDemoMode && tapes.length === 0 && <option value="TAPE_001">TAPE_001 (Demo)</option>}
                            </select>
                        </div>

                        <div className="pt-4 flex gap-3">
                            <button
                                onClick={reset}
                                className="flex-1 py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white text-xs font-bold uppercase tracking-wider rounded transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleExport}
                                disabled={!selectedTape || isExporting}
                                className={cn(
                                    "flex-1 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase tracking-wider rounded transition-colors flex items-center justify-center gap-2",
                                    (!selectedTape || isExporting) && "opacity-50 cursor-not-allowed"
                                )}
                            >
                                {isExporting ? (
                                    <>
                                        <span className="material-symbols-outlined text-sm animate-spin">sync</span>
                                        Exporting...
                                    </>
                                ) : (
                                    'Start Export'
                                )}
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="text-center py-4">
                        <div className={cn(
                            "size-16 rounded-full flex items-center justify-center mx-auto mb-4 border",
                            result.success ? "bg-primary/10 border-primary/20 text-primary" : "bg-destructive/10 border-destructive/20 text-destructive"
                        )}>
                            <span className="material-symbols-outlined text-4xl">{result.success ? 'check' : 'error'}</span>
                        </div>
                        <h3 className="text-white font-bold mb-2">{result.success ? 'Export Complete' : 'Export Failed'}</h3>
                        <p className="text-xs text-[#71717a] mb-6">{result.message}</p>
                        <button
                            onClick={reset}
                            className="w-full py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white text-xs font-bold uppercase tracking-wider rounded transition-colors"
                        >
                            Close
                        </button>
                    </div>
                )}
            </div>
        </div>
    )
}
