import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api, BackupSet, Snapshot, GraphData } from "@/lib/api"
import { ErrorState } from "@/components/ui/ErrorState"
import { EmptyState } from "@/components/ui/EmptyState"
import { Database, Clock, ChevronRight, Share2, Info } from "lucide-react"
import BackupSetTopology from "../components/BackupSetTopology";

export default function BackupSetsPage() {
    const [backupSets, setBackupSets] = useState<BackupSet[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [selectedSet, setSelectedSet] = useState<BackupSet | null>(null)
    const [snapshots, setSnapshots] = useState<Snapshot[]>([])
    const [graphData, setGraphData] = useState<GraphData | null>(null)
    const [activeTab, setActiveTab] = useState<'topology' | 'snapshots'>('topology')

    useEffect(() => {
        fetchBackupSets()
    }, [])

    const fetchBackupSets = async () => {
        setLoading(true)
        const res = await api.getBackupSets()
        if (res.success && res.data) {
            setBackupSets(res.data.backup_sets)
            if (res.data.backup_sets.length > 0 && !selectedSet) {
                handleSelectSet(res.data.backup_sets[0])
            }
        } else {
            setError(res.error || "Failed to fetch backup sets")
        }
        setLoading(false)
    }

    const handleSelectSet = async (set: BackupSet) => {
        setSelectedSet(set)
        setLoading(true)

        const [detailsRes, graphRes] = await Promise.all([
            api.getBackupSetDetails(set.id),
            api.getBackupSetGraph(set.id)
        ])

        if (detailsRes.success && detailsRes.data) {
            setSnapshots(detailsRes.data.snapshots)
        }

        if (graphRes.success && graphRes.data) {
            setGraphData(graphRes.data.graph)
        }

        setLoading(false)
    }

    if (loading && backupSets.length === 0) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
            </div>
        )
    }

    if (error) {
        return (
            <ErrorState
                title="Backup Set Linkage Error"
                message={error}
                retry={fetchBackupSets}
            />
        )
    }

    return (
        <div className="flex flex-col gap-6 h-full">
            <header>
                <h1 className="text-xl font-bold tracking-tight text-white uppercase antialiased">Tape Set Visualization</h1>
                <div className="flex items-center gap-2 mt-1">
                    <div className="size-1 rounded-full bg-primary ring-2 ring-primary/20"></div>
                    <span className="text-[10px] text-[#71717a] font-mono uppercase tracking-widest">Relational Topology Mapping</span>
                </div>
            </header>

            {backupSets.length === 0 ? (
                <EmptyState
                    title="No Backup Sets Found"
                    message="Complete your first backup job to generate a tape set topology."
                />
            ) : (
                <div className="grid grid-cols-12 gap-6 flex-1 min-h-0">
                    {/* Sidebar - Set List */}
                    <div className="col-span-12 lg:col-span-3 space-y-4">
                        <h2 className="text-[10px] font-bold text-white uppercase tracking-[0.2em] px-2 mb-4">Logical_Sets</h2>
                        <div className="space-y-2 overflow-y-auto max-h-[calc(100vh-250px)] pr-2 custom-scrollbar">
                            {backupSets.map(set => (
                                <button
                                    key={set.id}
                                    onClick={() => handleSelectSet(set)}
                                    className={cn(
                                        "w-full flex items-center justify-between p-4 rounded border transition-all text-left group",
                                        selectedSet?.id === set.id
                                            ? "bg-primary/10 border-primary/30 text-primary"
                                            : "bg-[#121214] border-[#27272a] text-[#71717a] hover:border-[#3f3f46] hover:text-white"
                                    )}
                                >
                                    <div className="flex flex-col gap-1">
                                        <div className="flex items-center gap-2">
                                            <Database className="size-3.5" />
                                            <span className="text-xs font-bold uppercase tracking-tight">{set.id}</span>
                                        </div>
                                        <span className="text-[9px] font-mono opacity-60">
                                            {set.sources.length} Sources
                                        </span>
                                    </div>
                                    <ChevronRight className={cn("size-4 opacity-0 group-hover:opacity-100 transition-opacity", selectedSet?.id === set.id && "opacity-100")} />
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Main Content - Detail View */}
                    <div className="col-span-12 lg:col-span-9 flex flex-col min-h-0">
                        {selectedSet && (
                            <div className="bg-[#121214] border border-[#27272a] rounded overflow-hidden flex flex-col flex-1">
                                {/* Header / Tabs */}
                                <div className="p-6 border-b border-[#27272a] bg-black/20 flex flex-col md:flex-row md:items-center justify-between gap-4">
                                    <div>
                                        <h2 className="text-lg font-bold text-white uppercase tracking-tight">{selectedSet.id}</h2>
                                        <div className="flex items-center gap-4 mt-2">
                                            <div className="flex items-center gap-1.5 text-[10px] text-[#71717a] font-mono uppercase">
                                                <Share2 className="size-3" />
                                                <span>Topology Graph</span>
                                            </div>
                                            <div className="size-1 rounded-full bg-[#27272a]"></div>
                                            <div className="flex items-center gap-1.5 text-[10px] text-[#71717a] font-mono uppercase">
                                                <Clock className="size-3" />
                                                <span>{snapshots.length} Snapshots</span>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="flex bg-black/60 p-1 rounded-lg border border-[#27272a]">
                                        <button
                                            onClick={() => setActiveTab('topology')}
                                            className={cn(
                                                "px-4 py-1.5 rounded text-[10px] font-bold uppercase tracking-widest transition-all",
                                                activeTab === 'topology'
                                                    ? "bg-primary text-black shadow-[0_0_15px_rgba(25,230,100,0.3)]"
                                                    : "text-[#71717a] hover:text-white"
                                            )}
                                        >
                                            Topology
                                        </button>
                                        <button
                                            onClick={() => setActiveTab('snapshots')}
                                            className={cn(
                                                "px-4 py-1.5 rounded text-[10px] font-bold uppercase tracking-widest transition-all",
                                                activeTab === 'snapshots'
                                                    ? "bg-primary text-black"
                                                    : "text-[#71717a] hover:text-white"
                                            )}
                                        >
                                            Snapshots
                                        </button>
                                    </div>
                                </div>

                                {/* Content Area */}
                                <div className="flex-1 min-h-[500px] relative overflow-hidden bg-[radial-gradient(#18181b_1px,transparent_1px)] [background-size:20px_20px]">
                                    {activeTab === 'topology' ? (
                                        graphData ? (
                                            <BackupSetTopology data={graphData} />
                                        ) : (
                                            <div className="absolute inset-0 flex items-center justify-center text-[#71717a] text-[10px] uppercase font-mono tracking-widest">
                                                Constructing Graph...
                                            </div>
                                        )
                                    ) : (
                                        <div className="p-6 space-y-4 max-h-full overflow-y-auto custom-scrollbar">
                                            {snapshots.map(snap => (
                                                <div key={snap.id} className="bg-black/40 border border-[#27272a] rounded p-4 flex items-center justify-between group hover:border-primary/20 transition-all">
                                                    <div className="flex items-center gap-6">
                                                        <div className="flex flex-col">
                                                            <span className="text-[10px] font-bold text-white uppercase tracking-tight">Snapshot_{snap.id}</span>
                                                            <span className="text-[10px] font-mono text-[#71717a]">{snap.created_at}</span>
                                                        </div>
                                                        <div className="h-8 w-px bg-[#27272a]"></div>
                                                        <div className="flex flex-col">
                                                            <span className="text-[10px] font-bold text-[#71717a] uppercase tracking-widest">Size</span>
                                                            <span className="text-xs font-mono text-white">{(snap.total_bytes / (1024 ** 3)).toFixed(2)} GB</span>
                                                        </div>
                                                        <div className="flex flex-col">
                                                            <span className="text-[10px] font-bold text-[#71717a] uppercase tracking-widest">Files</span>
                                                            <span className="text-xs font-mono text-white">{snap.total_files.toLocaleString()}</span>
                                                        </div>
                                                    </div>
                                                    <div className="flex items-center gap-3">
                                                        <div className="flex -space-x-2">
                                                            {Object.keys(snap.tape_map).map(barcode => (
                                                                <div
                                                                    key={barcode}
                                                                    title={barcode}
                                                                    className="size-7 rounded-full bg-[#18181b] border border-[#27272a] text-[8px] font-mono flex items-center justify-center text-primary shadow-[0_0_10px_rgba(0,0,0,0.5)]"
                                                                >
                                                                    {barcode.slice(-3)}
                                                                </div>
                                                            ))}
                                                        </div>
                                                        <button className="p-2 rounded hover:bg-white/5 text-[#71717a] hover:text-white transition-all">
                                                            <Info className="size-4" />
                                                        </button>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
