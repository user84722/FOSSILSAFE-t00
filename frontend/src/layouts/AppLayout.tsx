import { Link, Outlet, useLocation, useNavigate } from "react-router-dom"
import React, { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"
import HelpSystem from "@/components/HelpSystem"
import { useDemoMode } from "@/lib/demoMode"

const NavLink = ({ to, icon, label }: { to: string; icon: string; label: string }) => {
    const location = useLocation();
    const isActive = location.pathname === to;

    return (
        <Link
            to={to}
            className={cn(
                "h-10 w-full flex items-center justify-center rounded-md transition-all relative group",
                isActive
                    ? "bg-primary/10 text-primary border border-primary/20"
                    : "text-muted-foreground hover:text-foreground hover:bg-white/5"
            )}
        >
            <span className="material-symbols-outlined">{icon}</span>
            <div className="absolute left-14 ml-2 px-2 py-1 bg-[#121214] border border-[#27272a] rounded text-[10px] uppercase font-bold text-white opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50 shadow-xl">
                {label}
            </div>
        </Link>
    )
}

type SystemStatus = 'online' | 'warning' | 'offline';

const getStatusConfig = (systemStatus: SystemStatus) => {
    switch (systemStatus) {
        case 'online':
            return {
                label: 'System Online',
                bgClass: 'bg-primary/10 border-primary/20',
                dotClass: 'bg-primary shadow-[0_0_8px_rgba(25,230,100,0.6)]',
                textClass: 'text-primary'
            };
        case 'warning':
            return {
                label: 'Minor Issues',
                bgClass: 'bg-warning/10 border-warning/20',
                dotClass: 'bg-warning shadow-[0_0_8px_rgba(234,179,8,0.6)]',
                textClass: 'text-warning'
            };
        case 'offline':
        default:
            return {
                label: 'System Offline',
                bgClass: 'bg-destructive border-destructive shadow-[0_0_20px_rgba(239,68,68,0.5)]',
                dotClass: 'bg-white shadow-[0_0_8px_rgba(255,255,255,0.8)] animate-ping',
                textClass: 'text-white font-extrabold'
            };
    }
};

const SystemTicker = React.memo(({ stats }: { stats: any[] }) => {
    return (
        <div className="relative flex items-center h-full overflow-hidden flex-1 group transition-all">
            {/* Fade edges */}
            <div className="absolute inset-y-0 left-0 w-24 bg-gradient-to-r from-[#0c0c0e] via-[#0c0c0e]/80 to-transparent z-10"></div>
            <div className="absolute inset-y-0 right-0 w-24 bg-gradient-to-l from-[#0c0c0e] via-[#0c0c0e]/80 to-transparent z-10"></div>

            <div className="flex whitespace-nowrap animate-ticker group-hover:[animation-play-state:paused] py-1 will-change-transform">
                {[...stats, ...stats, ...stats].map((item, i) => (
                    <div key={i} className="flex items-center gap-2 px-6 border-r border-white/5 last:border-r-0 w-[240px] shrink-0 justify-start">
                        <span className={cn("material-symbols-outlined text-[12px] shrink-0", item.urgent ? "text-warning animate-pulse" : "text-white/10")}>{item.icon}</span>
                        <span className="text-[7px] font-bold text-white/20 uppercase tracking-[0.25em] w-20 shrink-0">{item.label}:</span>
                        <span className={cn("text-[9px] font-mono font-bold tracking-widest truncate shrink-0", item.urgent ? "text-warning" : "text-white/40")}>{item.value}</span>
                    </div>
                ))}
            </div>
        </div>
    );
});

export default function AppLayout() {
    const navigate = useNavigate();
    const { isDemoMode } = useDemoMode();
    const [systemStatus, setSystemStatus] = useState<SystemStatus>('offline');
    const [showUserMenu, setShowUserMenu] = useState(false);
    const [showHelp, setShowHelp] = useState(false);
    const [stats, setStats] = useState<any[]>(Array(8).fill({ label: 'LOADING', value: '---', icon: 'refresh' }));

    // Check system stats
    useEffect(() => {
        const fetchStats = async () => {
            if (isDemoMode) {
                setStats([
                    { label: 'UPTIME', value: '12D 4H 32M', icon: 'timer' },
                    { label: 'LOAD', value: '0.12 0.24 0.18', icon: 'analytics' },
                    { label: 'DRIVES', value: '1/2 BUSY', icon: 'settings_input_hdmi' },
                    { label: 'TAPES', value: '24', icon: 'hard_drive' },
                    { label: 'STORAGE', value: '450.2 TB', icon: 'database' },
                    { label: 'ACTIVE JOBS', value: '1', icon: 'sync' },
                    { label: 'MAINTENANCE', value: 'OPTIMAL', icon: 'cleaning_services' }
                ]);
                return;
            }

            try {
                const results = await Promise.allSettled([
                    api.getSystemInfo(),
                    api.getTapes(),
                    api.getJobs(),
                    api.getDriveHealth()
                ]);

                const [sysRes, tapeRes, jobRes, driveRes] = results.map(r =>
                    r.status === 'fulfilled' ? r.value : { success: false }
                ) as any[];

                const getUptime = () => {
                    if (!sysRes.success || !sysRes.data) return "---";
                    const upt = sysRes.data.uptime;
                    const days = Math.floor(upt / 86400);
                    const hours = Math.floor((upt % 86400) / 3600);
                    const mins = Math.floor((upt % 3600) / 60);
                    return `${days}D ${hours}H ${mins}M`;
                };

                const getLoad = () => {
                    if (!sysRes.success || !sysRes.data) return "---";
                    return sysRes.data.load_avg.map((l: number) => l.toFixed(2)).join(' ');
                };

                const getDrives = () => {
                    if (!driveRes.success || !driveRes.data?.drives) return "---";
                    const busy = driveRes.data.drives.filter((d: any) => d.status === 'busy').length;
                    return `${busy}/${driveRes.data.drives.length} BUSY`;
                };

                const getStorage = () => {
                    if (!tapeRes.success || !tapeRes.data?.tapes) return "---";
                    const used = tapeRes.data.tapes.reduce((acc: number, t: any) => acc + (t.used_bytes || 0), 0);
                    const tb = (used / (1024 ** 4)).toFixed(1);
                    return `${tb} TB`;
                };

                const isCleaningNeeded = driveRes.success && driveRes.data?.drives?.some((d: any) => d.cleaning_needed);

                const newStats = [
                    { label: 'UPTIME', value: getUptime(), icon: 'timer' },
                    { label: 'LOAD', value: getLoad(), icon: 'analytics' },
                    { label: 'DRIVES', value: getDrives(), icon: 'settings_input_hdmi' },
                    { label: 'TAPES', value: tapeRes.success ? tapeRes.data?.tapes?.length || 0 : "---", icon: 'hard_drive' },
                    { label: 'STORAGE', value: getStorage(), icon: 'database' },
                    { label: 'ACTIVE JOBS', value: jobRes.success ? jobRes.data?.jobs?.filter((j: any) => j.status === 'running').length || 0 : "---", icon: 'sync' },
                    {
                        label: 'MAINTENANCE',
                        value: isCleaningNeeded ? 'CLEANING REQ' : 'OPTIMAL',
                        icon: 'cleaning_services',
                        urgent: isCleaningNeeded
                    }
                ];

                setStats(newStats);
            } catch (err) {
                console.error("Failed to fetch ticker stats", err);
            }
        };

        const checkHealth = async () => {
            if (isDemoMode) {
                setSystemStatus('online');
                return;
            }
            try {
                const res = await api.getHealth();
                if (res.success && res.data) {
                    const status = res.data.status;
                    if (status === 'healthy') {
                        setSystemStatus('online');
                    } else if (status === 'degraded') {
                        setSystemStatus('warning');
                    } else {
                        setSystemStatus('offline');
                    }
                } else {
                    setSystemStatus('offline');
                }
            } catch {
                setSystemStatus('offline');
            }
        };

        checkHealth();
        fetchStats();
        const interval = setInterval(() => {
            checkHealth();
            fetchStats();
        }, 15000);
        return () => clearInterval(interval);
    }, [isDemoMode]);

    const handleLogout = () => {
        localStorage.removeItem('token');
        localStorage.removeItem('csrfToken');
        navigate('/login');
    };

    return (
        <div className="flex h-screen w-full bg-[#09090b] text-white overflow-hidden selection:bg-primary selection:text-black font-sans">
            {/* Sidebar (Collapsed 64px) */}
            <aside className="w-[64px] flex flex-col items-center bg-[#121214] border-r border-[#27272a] py-4 gap-6 shrink-0 z-20">
                {/* Logo Area - Clickable, navigates to dashboard */}
                <Link
                    to="/"
                    className="size-10 flex items-center justify-center rounded bg-primary/10 border border-primary/20 shadow-[0_0_10px_rgba(25,230,100,0.1)] mb-2 group cursor-pointer hover:bg-primary/20 transition-all duration-300 overflow-hidden"
                >
                    <img src="/fossilsafe-logo.svg" alt="FossilSafe" className="size-10 object-contain" />
                </Link>


                {/* Navigation */}
                <nav className="flex flex-col gap-2 w-full px-2">
                    <NavLink to="/" icon="grid_view" label="Dashboard" />
                    <NavLink to="/jobs" icon="list_alt" label="Jobs" />
                    <NavLink to="/schedules" icon="calendar_month" label="Schedules" />
                    <NavLink to="/tapes" icon="hard_drive" label="Tapes" />
                    <NavLink to="/restore" icon="settings_backup_restore" label="Restore" />
                    <NavLink to="/recovery" icon="medical_services" label="Disaster Recovery" />
                    <NavLink to="/equipment" icon="inventory_2" label="Equipment" />
                    <NavLink to="/self-test" icon="content_paste_search" label="Self-Test" />
                    <NavLink to="/backup-sets" icon="account_tree" label="Backup Sets" />
                    <NavLink to="/settings" icon="settings" label="Settings" />
                    <button
                        onClick={() => setShowHelp(true)}
                        className="h-10 w-full flex items-center justify-center rounded-md transition-all relative group text-muted-foreground hover:text-foreground hover:bg-white/5"
                    >
                        <span className="material-symbols-outlined">menu_book</span>
                        <div className="absolute left-14 ml-2 px-2 py-1 bg-[#121214] border border-[#27272a] rounded text-[10px] uppercase font-bold text-white opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50 shadow-xl">
                            MANUAL
                        </div>
                    </button>
                </nav>

                {/* User Menu */}
                <div className="mt-auto flex flex-col gap-4 items-center w-full px-2 relative">
                    <div className="h-px w-8 bg-[#27272a]"></div>
                    <div className="relative">
                        <button
                            onClick={() => setShowUserMenu(!showUserMenu)}
                            className="h-10 w-full flex items-center justify-center rounded-md text-muted-foreground hover:text-white hover:bg-white/5 transition-colors"
                        >
                            <span className="material-symbols-outlined">account_circle</span>
                        </button>

                        {/* User Dropdown Menu */}
                        {showUserMenu && (
                            <div className="absolute bottom-12 left-0 w-40 bg-[#121214] border border-[#27272a] rounded-md shadow-xl z-50 overflow-hidden">
                                <div className="px-3 py-2 border-b border-[#27272a] bg-[#18181b]/50">
                                    <span className="text-[10px] text-muted-foreground font-mono uppercase tracking-wider">Logged in as</span>
                                    <p className="text-xs font-bold text-white">admin</p>
                                </div>
                                <Link
                                    to="/settings?tab=Users"
                                    onClick={() => setShowUserMenu(false)}
                                    className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-white/5 hover:text-white transition-colors"
                                >
                                    <span className="material-symbols-outlined text-sm">settings</span>
                                    User Settings
                                </Link>
                                <button
                                    onClick={handleLogout}
                                    className="w-full flex items-center gap-2 px-3 py-2 text-xs text-destructive hover:bg-destructive/10 transition-colors"
                                >
                                    <span className="material-symbols-outlined text-sm">logout</span>
                                    Logout
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </aside>

            {/* Main Content Area */}
            <main className="flex-1 flex flex-col h-full overflow-hidden relative">
                {/* Header */}
                <header className="h-16 border-b border-[#27272a] bg-[#09090b]/80 backdrop-blur-md flex items-center justify-between px-6 shrink-0 z-10">
                    <div className="flex items-center gap-3">
                        <h1 className="text-xl font-bold tracking-[0.1em] text-white uppercase antialiased">
                            FossilSafe
                        </h1>
                    </div>

                    <div className="flex items-center gap-6">
                        {/* Status Pill - Dynamic based on actual system health */}
                        <div className={cn("flex items-center gap-2 px-3 py-1.5 rounded-full border shrink-0", getStatusConfig(systemStatus).bgClass)}>
                            <div className={cn("size-2 rounded-full animate-pulse", getStatusConfig(systemStatus).dotClass)}></div>
                            <span className={cn("text-[10px] font-bold tracking-widest uppercase text-white shadow-sm", getStatusConfig(systemStatus).textClass)}>{getStatusConfig(systemStatus).label}</span>
                        </div>
                    </div>
                </header>

                {/* Dashboard/Page Content */}
                <div className="flex-1 overflow-y-auto p-6 scroll-smooth custom-scrollbar">
                    <Outlet />
                </div>

                <footer className="h-8 flex shrink-0 items-center justify-between border-t border-[#27272a] bg-[#0c0c0e] px-6">
                    <div className="flex items-center gap-4 text-[11px] font-mono uppercase tracking-[0.2em]">
                        <a href="https://fossilsafe.io" target="_blank" rel="noopener noreferrer" className="text-muted-foreground hover:text-primary transition-colors font-bold">
                            fossilsafe.io
                        </a>
                    </div>

                    <div className="flex-1 max-w-[800px] h-full">
                        <SystemTicker stats={stats} />
                    </div>

                    <div className="w-24 flex justify-end">
                    </div>
                </footer>
            </main>

            {/* Click outside to close user menu */}
            {showUserMenu && (
                <div
                    className="fixed inset-0 z-10"
                    onClick={() => setShowUserMenu(false)}
                />
            )}
            <HelpSystem
                isOpen={showHelp}
                onClose={() => setShowHelp(false)}
            />
        </div>
    )
}
