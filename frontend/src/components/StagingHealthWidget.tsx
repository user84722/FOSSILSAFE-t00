import { Job } from "@/lib/api";
import { cn } from "@/lib/utils";

interface StagingHealthWidgetProps {
    job: Job | null;
}

export function StagingHealthWidget({ job }: StagingHealthWidgetProps) {
    if (!job || (job.status !== 'running' && job.status !== 'pending')) {
        return (
            <div className="bg-[#121214] border border-[#27272a] rounded-md p-4 flex flex-col justify-between group h-full">
                <div className="flex justify-between items-start mb-2">
                    <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-widest text-[#71717a]">Staging Health</span>
                    <span className="material-symbols-outlined text-[#3f3f46] text-xl">database</span>
                </div>
                <div className="flex flex-col gap-1">
                    <span className="text-sm font-bold text-muted-foreground italic">Staging Idle</span>
                </div>
                <div className="mt-3 w-full bg-[#1c1c1e] rounded-full h-1">
                    <div className="bg-[#3f3f46] h-full rounded-full" style={{ width: `0%` }}></div>
                </div>
            </div>
        );
    }

    const formatSpeed = (bps: number | undefined) => {
        if (bps === undefined || bps === null) return "0.0 MB/s";
        const mb = bps / (1024 * 1024);
        if (mb >= 1000) return `${(mb / 1024).toFixed(2)} GB/s`;
        return `${mb.toFixed(1)} MB/s`;
    };

    const health = job.buffer_health || 0;
    const isUnderrun = health < 0.1 && job.status === 'running';
    const isOptimal = health > 0.4;

    return (
        <div className="bg-[#121214] border border-[#27272a] rounded-md p-4 flex flex-col justify-between group hover:border-[#38bdf8]/30 transition-colors h-full relative overflow-hidden">
            {/* Pulsing indicator for underrun */}
            {isUnderrun && (
                <div className="absolute inset-0 bg-destructive/5 animate-pulse pointer-events-none"></div>
            )}

            <div className="flex justify-between items-start mb-2">
                <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-widest text-[#71717a]">Pipeline Engine</span>
                <div className="flex gap-1 items-center">
                    <div className={cn(
                        "w-1.5 h-1.5 rounded-full animate-pulse",
                        isUnderrun ? "bg-destructive shadow-[0_0_8px_red]" :
                            isOptimal ? "bg-[#38bdf8] shadow-[0_0_8px_#38bdf8]" : "bg-warning shadow-[0_0_8px_orange]"
                    )}></div>
                    <span className="text-[9px] font-mono text-[#71717a] uppercase tracking-tighter">
                        {isUnderrun ? "Underrun Risk" : isOptimal ? "Streaming Optimal" : "Buffering"}
                    </span>
                </div>
            </div>

            <div className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-2">
                    <div className="flex flex-col">
                        <span className="text-[9px] text-[#71717a] uppercase font-bold tracking-wider mb-0.5">Feeder</span>
                        <span className="text-xs font-mono font-bold text-white">{formatSpeed(job.feeder_rate_bps)}</span>
                    </div>
                    <div className="flex flex-col items-end">
                        <span className="text-[9px] text-[#71717a] uppercase font-bold tracking-wider mb-0.5">Ingest</span>
                        <span className="text-xs font-mono font-bold text-[#38bdf8]">{formatSpeed(job.ingest_rate_bps)}</span>
                    </div>
                </div>

                <div className="space-y-1.5">
                    <div className="flex justify-between items-center text-[9px] uppercase font-bold tracking-wider">
                        <span className="text-[#71717a]">NVMe Buffer Health</span>
                        <span className={cn(
                            "font-mono",
                            isUnderrun ? "text-destructive" : isOptimal ? "text-[#38bdf8]" : "text-warning"
                        )}>
                            {(health * 100).toFixed(0)}%
                        </span>
                    </div>
                    <div className="w-full bg-[#1c1c1e] rounded-full h-1.5 overflow-hidden">
                        <div
                            className={cn(
                                "h-full rounded-full transition-all duration-700",
                                isUnderrun ? "bg-destructive" : isOptimal ? "bg-[#38bdf8]" : "bg-warning"
                            )}
                            style={{ width: `${health * 100}%` }}
                        ></div>
                    </div>
                </div>
            </div>

            <div className="mt-2 flex justify-center">
                <span className="text-[8px] font-mono text-[#3f3f46] uppercase tracking-[0.2em]">Hardware Staging Active</span>
            </div>
        </div>
    );
}
