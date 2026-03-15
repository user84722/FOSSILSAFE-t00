import { useState, useMemo } from "react"
import { cn } from "@/lib/utils"
import { Tape } from "@/lib/api"
import { Search, Inbox, Check, Disc, CassetteTape, ShieldAlert } from "lucide-react"

interface TapeSelectProps {
    tapes: Tape[]
    selectedBarcode: string
    onSelect: (barcode: string) => void
    filter?: (tape: Tape) => boolean
    className?: string
}

export function TapeSelect({ tapes, selectedBarcode, onSelect, filter, className }: TapeSelectProps) {
    const [search, setSearch] = useState("")

    const filteredTapes = useMemo(() => {
        let result = tapes || []

        if (filter) {
            result = result.filter(filter)
        }

        if (search) {
            const lower = search.toLowerCase()
            result = result.filter(t =>
                (t.barcode?.toLowerCase() || "").includes(lower) ||
                (t.alias?.toLowerCase() || "").includes(lower)
            )
        }

        return result
    }, [tapes, filter, search])

    const isEmpty = filteredTapes.length === 0

    return (
        <div className={cn("flex flex-col gap-3 h-full", className)}>
            <div className="relative shrink-0">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[#71717a] size-4" />
                <input
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Filter tapes..."
                    className="w-full bg-black/40 border border-[#27272a] rounded pl-9 pr-4 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none transition-all placeholder:text-[#3f3f46]"
                />
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar p-1 space-y-2">
                {isEmpty ? (
                    <div className="h-full flex flex-col items-center justify-center text-center border border-dashed border-[#27272a] rounded opacity-50 p-8">
                        <Inbox className="size-10 text-[#71717a] mb-2" />
                        <p className="text-xs text-[#71717a]">No tapes found</p>
                    </div>
                ) : (
                    filteredTapes.map(tape => {
                        const isSelected = selectedBarcode === tape.barcode
                        const isError = (tape.error_count || 0) > 0 || tape.status === 'error'
                        const isUntrusted = tape.trust_status === 'untrusted'

                        // Calculate usage percentage
                        const capacity = tape.capacity_bytes || 0
                        const used = tape.used_bytes || 0
                        const percent = capacity > 0 ? Math.round((used / capacity) * 100) : 0

                        return (
                            <button
                                key={tape.barcode}
                                onClick={() => onSelect(tape.barcode)}
                                className={cn(
                                    "relative flex items-center gap-4 p-4 rounded-md border text-left transition-all group overflow-hidden w-full shrink-0",
                                    isSelected
                                        ? "bg-primary/5 border-primary ring-1 ring-primary/50 shadow-[0_0_20px_rgba(25,230,100,0.1)]"
                                        : isError
                                            ? "bg-destructive/5 border-destructive/50 hover:bg-destructive/10"
                                            : "bg-[#121214] border-[#27272a] hover:bg-[#18181b] hover:border-[#52525b]"
                                )}
                            >
                                {isSelected && (
                                    <div className="absolute top-0 right-0 p-1 bg-primary text-black rounded-bl shadow-sm z-10">
                                        <Check className="size-3 stroke-[3]" />
                                    </div>
                                )}

                                {/* Icon Section */}
                                <div className={cn(
                                    "size-12 rounded-md flex items-center justify-center shrink-0 border transition-colors",
                                    isSelected ? "bg-primary/20 text-primary border-primary/30" :
                                        isError ? "bg-destructive/20 text-destructive border-destructive/30" :
                                            "bg-[#18181b] text-[#52525b] border-[#27272a] group-hover:text-[#a1a1aa] group-hover:border-[#3f3f46]"
                                )}>
                                    {tape.location_type === 'drive' ? (
                                        <Disc className="size-6" />
                                    ) : (
                                        <CassetteTape className="size-6" />
                                    )}
                                </div>

                                {/* Main Info Section */}
                                <div className="flex-1 min-w-0 flex flex-col justify-center">
                                    <div className="flex items-center gap-2">
                                        <span className={cn(
                                            "font-mono text-base font-bold truncate tracking-tight leading-none",
                                            isSelected ? "text-primary" : "text-white"
                                        )}>
                                            {tape.barcode}
                                        </span>
                                        {isUntrusted && (
                                            <ShieldAlert className="size-4 text-destructive" strokeWidth={2.5} />
                                        )}
                                    </div>
                                    <div className="text-xs text-[#71717a] font-medium truncate mt-1">
                                        {tape.alias || 'No Alias'}
                                    </div>
                                </div>

                                {/* Metrics Section */}
                                <div className="hidden sm:flex flex-col gap-2 w-32 shrink-0">
                                    <div className="space-y-1">
                                        <div className="flex justify-between text-[9px] uppercase font-bold text-[#52525b]">
                                            <span>Usage</span>
                                            <span>{percent}%</span>
                                        </div>
                                        <div className="h-1.5 w-full bg-[#09090b] rounded-full overflow-hidden border border-[#27272a]">
                                            <div
                                                className={cn("h-full rounded-full transition-all",
                                                    isSelected ? "bg-primary" : "bg-[#52525b] group-hover:bg-[#a1a1aa]"
                                                )}
                                                style={{ width: `${percent}%` }}
                                            ></div>
                                        </div>
                                    </div>

                                    <div className="flex items-center gap-1">
                                        <span className={cn(
                                            "text-[9px] uppercase font-bold tracking-wider px-2 py-0.5 rounded border flex-1 text-center truncate",
                                            tape.status === 'available' ? "bg-emerald-500/5 text-emerald-500 border-emerald-500/20" :
                                                tape.status === 'in_use' ? "bg-blue-500/5 text-blue-500 border-blue-500/20" :
                                                    tape.status === 'full' ? "bg-amber-500/5 text-amber-500 border-amber-500/20" :
                                                        "bg-[#27272a] text-[#71717a] border-transparent"
                                        )}>
                                            {tape.status.replace('_', ' ')}
                                        </span>
                                        {tape.location_type === 'drive' && (
                                            <span className="text-[9px] uppercase font-bold tracking-wider px-2 py-0.5 rounded border bg-[#27272a] text-[#a1a1aa] border-[#3f3f46]">
                                                Drive
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </button>
                        )
                    })
                )}
            </div>
        </div>
    )
}
