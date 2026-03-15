import { Job } from "@/lib/api";


interface ThroughputWidgetProps {
    job: Job | null;
}

export function ThroughputWidget({ job }: ThroughputWidgetProps) {
    const isRunning = job && (job.status === 'running' || job.status === 'pending');

    if (!job || !isRunning) {
        return (
            <div className="bg-[#121214] border border-[#27272a] rounded-md p-4 flex flex-col justify-between group hover:border-[#3f3f46] transition-colors h-full">
                <div className="flex justify-between items-start mb-2">
                    <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-widest text-[#71717a]">Active Job</span>
                    <span className="material-symbols-outlined text-muted-foreground text-xl">check_circle</span>
                </div>
                <div className="flex flex-col gap-1">
                    <span className="text-sm font-bold text-muted-foreground">No Active Job</span>
                    <span className="text-[10px] text-muted-foreground font-mono uppercase tracking-wider">System Idle</span>
                </div>
                <div className="mt-3 w-full bg-gray-800 rounded-full h-1 overflow-hidden">
                    <div className="bg-primary h-full rounded-full" style={{ width: `0%` }}></div>
                </div>
            </div>
        )
    }

    const formatSpeed = (bytesPerSec: number | undefined) => {
        if (!bytesPerSec) return ["0.0", "MB/s"];
        const mb = bytesPerSec / (1024 * 1024);
        if (mb >= 1000) {
            return [(mb / 1024).toFixed(2), "GB/s"];
        }
        return [mb.toFixed(1), "MB/s"];
    };

    const formatEta = (seconds: number | undefined) => {
        if (seconds === undefined || seconds === null) return "--:--";
        if (seconds === 0) return "Finishing...";
        if (seconds >= 3600) {
            const h = Math.floor(seconds / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            return `${h}h ${m}m`;
        }
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${m}m ${s}s`;
    };

    const [speedVal, speedUnit] = formatSpeed(job.bytes_per_second);

    return (
        <div className="bg-[#121214] border border-[#27272a] rounded-md p-4 flex flex-col justify-between group hover:border-primary/30 transition-colors relative overflow-hidden h-full">
            {/* Background pulse effect */}
            <div className="absolute -top-10 -right-10 w-32 h-32 bg-primary/5 blur-3xl rounded-full animate-pulse pointer-events-none"></div>

            <div className="flex justify-between items-start mb-2">
                <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-widest text-[#71717a]">Active Job</span>
                <span className="material-symbols-outlined text-warning text-xl animate-spin" style={{ animationDuration: '3s', animationDirection: 'reverse' }}>sync</span>
            </div>

            <div className="flex flex-col gap-1 z-10 w-full">
                <div className="flex justify-between items-baseline mb-1">
                    <span className="text-sm font-bold text-white truncate max-w-[140px]" title={job.name}>{job.name}</span>
                    <span className="text-[10px] text-primary font-mono uppercase tracking-wider">{job.type} {job.progress}%</span>
                </div>

                <div className="w-full bg-gray-800 rounded-full h-1 overflow-hidden mb-3">
                    <div className="bg-primary h-full rounded-full transition-all duration-500" style={{ width: `${job.progress}%` }}></div>
                </div>

                <div className="flex justify-between items-end border-t border-[#27272a]/50 pt-2">
                    <div className="flex flex-col">
                        <span className="text-[9px] text-[#71717a] uppercase font-bold tracking-wider">Speed</span>
                        <div className="flex items-baseline gap-1">
                            <span className="text-sm font-bold font-mono text-white">{speedVal}</span>
                            <span className="text-[9px] text-muted-foreground font-mono">{speedUnit}</span>
                        </div>
                    </div>
                    <div className="flex flex-col items-end">
                        <span className="text-[9px] text-[#71717a] uppercase font-bold tracking-wider">ETA</span>
                        <span className="text-sm font-mono text-primary font-bold">{formatEta(job.eta_seconds)}</span>
                    </div>
                </div>
            </div>
        </div>
    );
}
