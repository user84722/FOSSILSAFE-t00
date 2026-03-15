import { useState, useEffect } from "react"
import { api } from "@/lib/api"
import {
    Activity,
    ShieldCheck,
    AlertTriangle,
    History,
    Trash2,
    Play,
    RefreshCcw,
    CheckCircle2,
    XCircle,
    Info,
    ChevronDown,
    ChevronUp,
    FileJson,
    FileText,
    Cpu,
    HardDrive
} from "lucide-react"
import { cn } from "@/lib/utils"

import { useSocket } from "@/contexts/SocketProvider"
import ConfirmationModal from "@/components/ui/ConfirmationModal"



interface DiagnosticReport {
    id: number;
    timestamp: string;
    overall_status: 'pass' | 'fail' | 'warning';
    summary: string;
    report_json_path: string;
    report_text_path: string;
}

export default function SelfTestPage() {
    const { socket } = useSocket();
    const [reports, setReports] = useState<DiagnosticReport[]>([]);
    const [lastResult, setLastResult] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [running, setRunning] = useState(false);
    const [expandedReport, setExpandedReport] = useState<number | null>(null);
    const [deleteModal, setDeleteModal] = useState<{ isOpen: boolean; reportId: number | null }>({
        isOpen: false,
        reportId: null
    });

    const fetchReports = async () => {
        try {
            const res = await api.getDiagnosticsReports(10);
            if (res.success && res.data) {
                setReports(res.data.reports);
            }
        } catch (err) {
            console.error("Failed to fetch diagnostic reports", err);
        } finally {
            setLoading(false);
        }
    };

    const fetchHealth = async () => {
        try {
            const res = await api.getDiagnosticsHealth();
            if (res.success && res.data) {
                setLastResult(res.data);
            }
        } catch (err) {
            console.error("Failed to fetch health check", err);
        }
    };

    useEffect(() => {
        fetchReports();
        fetchHealth();
    }, []);

    useEffect(() => {
        if (!socket) return;
        const handleUpdate = (data: any) => {
            if (data.type === 'diagnostics_run') {
                if (data.status === 'completed' || data.status === 'error') {
                    setRunning(false);
                    fetchReports();
                    fetchHealth();
                }
            }
        };
        socket.on('job_status_change', handleUpdate);
        return () => {
            socket.off('job_status_change', handleUpdate);
        };
    }, [socket]);

    const runSelfTest = async () => {
        setRunning(true);
        try {
            const res = await api.runDiagnostics();
            if (!res.success) {
                setRunning(false);
                alert("Failed to start self-test: " + res.error);
            }
        } catch (err) {
            setRunning(false);
            console.error("Failed to run self-test", err);
        }
    };

    const deleteReport = async (reportId: number) => {
        try {
            const res = await api.deleteDiagnosticsReport(reportId);
            if (res.success) {
                setReports(reports.filter(r => r.id !== reportId));
            }
        } catch (err) {
            console.error("Failed to delete report", err);
        }
        setDeleteModal({ isOpen: false, reportId: null });
    };

    const downloadReport = async (reportId: number, kind: 'json' | 'text') => {
        const url = await api.getDiagnosticsReportDownloadUrl(reportId, kind);
        window.open(url, '_blank');
    };

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'pass': return <CheckCircle2 className="size-4 text-primary" />;
            case 'fail': return <XCircle className="size-4 text-destructive" />;
            case 'warning': return <AlertTriangle className="size-4 text-warning" />;
            default: return <Info className="size-4 text-muted-foreground" />;
        }
    };

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'pass': return 'text-primary bg-primary/10 border-primary/20';
            case 'fail': return 'text-destructive bg-destructive/10 border-destructive/20';
            case 'warning': return 'text-warning bg-warning/10 border-warning/20';
            default: return 'text-muted-foreground bg-muted/10 border-muted/20';
        }
    };

    return (
        <div className="flex flex-col gap-6 animate-in fade-in duration-500">
            {/* Header / Actions */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-[#121214] border border-[#27272a] rounded-md p-6 overflow-hidden relative group">
                <div className="absolute -right-12 -top-12 size-48 bg-primary/5 blur-3xl rounded-full group-hover:bg-primary/10 transition-colors"></div>

                <div className="flex items-center gap-4 relative z-10">
                    <div className="size-12 rounded bg-primary/10 border border-primary/30 flex items-center justify-center text-primary shadow-[0_0_15px_rgba(25,230,100,0.1)]">
                        <Activity className="size-6" />
                    </div>
                    <div>
                        <h1 className="text-lg font-bold text-white uppercase tracking-widest">Appliance Self-Test</h1>
                        <p className="text-xs text-[#71717a] font-medium font-mono uppercase tracking-tight">System hardware, permissions, and tape path verification</p>
                    </div>
                </div>

                <div className="flex items-center gap-3 relative z-10">
                    <button
                        onClick={runSelfTest}
                        disabled={running}
                        className={cn(
                            "px-6 py-2.5 rounded text-[10px] font-bold uppercase tracking-widest transition-all flex items-center gap-2 border",
                            running
                                ? "bg-zinc-800 border-zinc-700 text-zinc-500 cursor-wait"
                                : "bg-primary text-black border-primary hover:bg-green-400 shadow-glow"
                        )}
                    >
                        {running ? <RefreshCcw className="size-3.5 animate-spin" /> : <Play className="size-3.5 fill-current" />}
                        {running ? "Self-Test Running..." : "Execute Full Self-Test"}
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Current Status Highlights */}
                <div className="lg:col-span-1 flex flex-col gap-6">
                    <div className="bg-[#121214] border border-[#27272a] rounded-md overflow-hidden">
                        <div className="px-4 py-3 border-b border-[#27272a] bg-[#18181b]/50 flex justify-between items-center">
                            <h2 className="text-[10px] font-bold text-white uppercase tracking-[0.2em] flex items-center gap-2">
                                <ShieldCheck className="text-primary size-3.5" />
                                Current Health
                            </h2>
                            {lastResult && (
                                <span className={cn(
                                    "text-[8px] font-bold px-1.5 py-0.5 rounded border uppercase tracking-widest",
                                    getStatusColor(lastResult.overall || 'pass')
                                )}>
                                    {lastResult.overall || 'Stable'}
                                </span>
                            )}
                        </div>
                        <div className="p-4 flex flex-col gap-3">
                            {lastResult?.checks?.map((check: any, idx: number) => (
                                <div key={idx} className="flex items-center justify-between p-2 rounded bg-black/40 border border-[#27272a] group hover:border-white/10 transition-colors">
                                    <div className="flex items-center gap-2">
                                        {getStatusIcon(check.status)}
                                        <div className="flex flex-col">
                                            <span className="text-[10px] font-bold text-white uppercase tracking-wider">{check.name}</span>
                                            <span className="text-[9px] text-[#71717a] font-medium">{check.message}</span>
                                        </div>
                                    </div>
                                    {check.status === 'warning' && <AlertTriangle className="size-3 text-warning" />}
                                </div>
                            )) || (
                                    <div className="text-center py-6">
                                        <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-widest">No recent health data</span>
                                    </div>
                                )}
                        </div>
                    </div>

                    <div className="bg-[#121214] border border-[#27272a] rounded-md p-4 flex flex-col gap-4">
                        <div className="flex flex-col gap-1">
                            <span className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Hardware Statistics</span>
                            <div className="h-px w-full bg-[#27272a]"></div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="flex flex-col gap-1">
                                <div className="flex items-center gap-1.5 text-muted-foreground">
                                    <Cpu className="size-3" />
                                    <span className="text-[8px] font-bold uppercase tracking-wider">Load Avg</span>
                                </div>
                                <span className="text-sm font-mono font-bold text-white">{lastResult?.checks?.find((c: any) => c.name === 'System Load')?.message?.split(': ')[1] || '---'}</span>
                            </div>
                            <div className="flex flex-col gap-1">
                                <div className="flex items-center gap-1.5 text-muted-foreground">
                                    <HardDrive className="size-3" />
                                    <span className="text-[8px] font-bold uppercase tracking-wider">Free Space</span>
                                </div>
                                <span className="text-sm font-mono font-bold text-white">{lastResult?.checks?.find((c: any) => c.name === 'Disk Space')?.message?.split(' ')[0] || '---'} GB</span>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Historical Reports */}
                <div className="lg:col-span-2">
                    <div className="bg-[#121214] border border-[#27272a] rounded-md overflow-hidden h-full">
                            <div className="px-4 py-3 border-b border-[#27272a] bg-[#18181b]/50">
                                <h2 className="text-[10px] font-bold text-white uppercase tracking-[0.2em] flex items-center gap-2">
                                    <History className="text-primary size-3.5" />
                                    Diagnostic History
                                </h2>
                            </div>

                            <div className="flex flex-col">
                                {loading ? (
                                    <div className="flex items-center justify-center py-20">
                                        <RefreshCcw className="size-6 text-primary animate-spin" />
                                    </div>
                                ) : reports.length === 0 ? (
                                    <div className="flex flex-col items-center justify-center py-20 gap-3 grayscale opacity-30">
                                        <History className="size-12" />
                                        <span className="text-[10px] font-bold uppercase tracking-widest">No diagnostic reports stored</span>
                                    </div>
                                ) : (
                                    <div className="divide-y divide-[#27272a]">
                                        {reports.map((report) => (
                                            <div key={report.id} className="flex flex-col">
                                                <div className="p-4 flex items-center justify-between hover:bg-white/5 transition-colors group">
                                                    <div className="flex items-center gap-4">
                                                        <div className={cn(
                                                            "size-8 rounded flex items-center justify-center border",
                                                            getStatusColor(report.overall_status)
                                                        )}>
                                                            {getStatusIcon(report.overall_status)}
                                                        </div>
                                                        <div className="flex flex-col">
                                                            <div className="flex items-center gap-2">
                                                                <span className="text-[11px] font-bold text-white font-mono uppercase">ID-{report.id}</span>
                                                                <span className="text-[9px] text-[#71717a] font-bold uppercase tracking-wider">{new Date(report.timestamp).toLocaleString()}</span>
                                                            </div>
                                                            <span className="text-[10px] text-zinc-400 font-medium">{report.summary}</span>
                                                        </div>
                                                    </div>

                                                    <div className="flex items-center gap-2">
                                                        <div className="hidden group-hover:flex items-center gap-1">
                                                            <button
                                                                onClick={() => downloadReport(report.id, 'json')}
                                                                className="size-7 flex items-center justify-center rounded bg-black border border-[#27272a] hover:border-primary text-muted-foreground hover:text-primary transition-all"
                                                                title="Download JSON Report"
                                                            >
                                                                <FileJson className="size-3.5" />
                                                            </button>
                                                            <button
                                                                onClick={() => downloadReport(report.id, 'text')}
                                                                className="size-7 flex items-center justify-center rounded bg-black border border-[#27272a] hover:border-primary text-muted-foreground hover:text-primary transition-all"
                                                                title="Download Text Report"
                                                            >
                                                                <FileText className="size-3.5" />
                                                            </button>
                                                            <button
                                                                onClick={() => setDeleteModal({ isOpen: true, reportId: report.id })}
                                                                className="size-7 flex items-center justify-center rounded bg-black border border-[#27272a] hover:border-destructive text-muted-foreground hover:text-destructive transition-all"
                                                                title="Delete Report"
                                                            >
                                                                <Trash2 className="size-3.5" />
                                                            </button>
                                                        </div>
                                                        <button
                                                            onClick={() => setExpandedReport(expandedReport === report.id ? null : report.id)}
                                                            className="size-7 flex items-center justify-center rounded bg-[#18181b] border border-[#27272a] text-[#71717a] hover:text-white transition-colors"
                                                        >
                                                            {expandedReport === report.id ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
                                                        </button>
                                                    </div>
                                                </div>

                                                {expandedReport === report.id && (
                                                    <div className="px-4 pb-4 animate-in slide-in-from-top-2 duration-300">
                                                        <div className="bg-black/40 border border-[#27272a] rounded p-3 font-mono text-[9px] text-[#71717a] flex flex-col gap-2">
                                                            <div className="flex justify-between items-center border-b border-[#27272a] pb-1">
                                                                <span className="uppercase font-bold tracking-widest text-primary/70">System Paths</span>
                                                            </div>
                                                            <div className="flex flex-col gap-1">
                                                                <div className="flex justify-between">
                                                                    <span>JSON Output:</span>
                                                                    <span className="text-zinc-300 truncate ml-4">{report.report_json_path}</span>
                                                                </div>
                                                                <div className="flex justify-between">
                                                                    <span>Text Output:</span>
                                                                    <span className="text-zinc-300 truncate ml-4">{report.report_text_path}</span>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>


            <ConfirmationModal
                isOpen={deleteModal.isOpen}
                onClose={() => setDeleteModal({ isOpen: false, reportId: null })}
                onConfirm={() => deleteModal.reportId && deleteReport(deleteModal.reportId)}
                title="Delete Diagnostic Report"
                message="Are you sure you want to permanently delete this diagnostic report and its associated files from the appliance? This action cannot be undone."
                variant="danger"
            />
        </div >
    )
}
