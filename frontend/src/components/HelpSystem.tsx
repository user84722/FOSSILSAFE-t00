import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"

export default function HelpSystem({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
    const [activeTab, setActiveTab] = useState('overview')

    // Prevent scrolling when open
    useEffect(() => {
        if (isOpen) document.body.style.overflow = 'hidden'
        else document.body.style.overflow = 'unset'
        return () => { document.body.style.overflow = 'unset' }
    }, [isOpen])

    if (!isOpen) return null

    const TABS = [
        { id: 'overview', label: 'Overview', icon: 'info' },
        { id: 'install', label: 'Installation', icon: 'download' },
        { id: 'concepts', label: 'Core Concepts', icon: 'school' },
        { id: 'hardware', label: 'Hardware & Tapes', icon: 'hard_drive' },
        { id: 'jobs', label: 'Jobs & Automation', icon: 'calendar_clock' },
        { id: 'admin', label: 'Settings & Admin', icon: 'admin_panel_settings' },
        { id: 'cli', label: 'CLI Reference', icon: 'terminal' },
        { id: 'recovery', label: 'Disaster Recovery', icon: 'medical_services' },
        { id: 'troubleshoot', label: 'Troubleshooting', icon: 'build_circle' },
    ]

    return (
        <div className="fixed inset-0 z-[100] flex justify-end">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/50 backdrop-blur-sm pointer-events-auto transition-opacity"
                onClick={onClose}
            ></div>

            {/* Panel */}
            <div className="w-[1000px] h-full bg-[#09090b] border-l border-[#27272a] shadow-2xl pointer-events-auto flex flex-col transition-transform duration-300 transform translate-x-0">
                <div className="flex items-center justify-between p-6 border-b border-[#27272a] bg-[#121214]">
                    <h2 className="text-xl font-bold text-white flex items-center gap-3 uppercase tracking-wide">
                        <span className="material-symbols-outlined text-primary text-2xl">menu_book</span>
                        FossilSafe Operator Manual
                    </h2>
                    <button onClick={onClose} className="text-[#a1a1aa] hover:text-white transition-colors">
                        <span className="material-symbols-outlined text-2xl">close</span>
                    </button>
                </div>

                <div className="flex h-full overflow-hidden">
                    {/* Sidebar */}
                    <div className="w-64 bg-[#0c0c0e] border-r border-[#27272a] flex flex-col pt-4 shrink-0 overflow-y-auto">
                        {TABS.map(tab => (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id)}
                                className={cn(
                                    "px-5 py-4 flex items-center gap-3 text-[12px] font-bold uppercase tracking-wider text-left transition-colors border-l-2",
                                    activeTab === tab.id
                                        ? "bg-white/5 text-primary border-primary"
                                        : "text-[#71717a] border-transparent hover:text-white hover:bg-white/5"
                                )}
                            >
                                <span className="material-symbols-outlined text-xl">{tab.icon}</span>
                                {tab.label}
                            </button>
                        ))}
                    </div>

                    {/* Content */}
                    <div className="flex-1 overflow-y-auto p-10 custom-scrollbar bg-[#09090b]">
                        {activeTab === 'overview' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">System Architecture</h3>
                                    <div className="text-sm text-[#a1a1aa] leading-relaxed space-y-6 font-sans">
                                        <p>
                                            FossilSafe is an industrial-grade appliance for LTO tape management. It is designed to run on a dedicated server connected via SAS or Fibre Channel to professional tape hardware.
                                            It abstracts the complexity of <code>mt-st</code> (magnetic tape control) and <code>ltfs</code> (Linear Tape File System) into a modern, premium web interface.
                                        </p>

                                        <div className="bg-[#121214] border border-[#27272a] p-6 rounded-lg grid grid-cols-2 gap-8">
                                            <div>
                                                <h4 className="text-white font-bold uppercase text-xs mb-4">Software Stack</h4>
                                                <ul className="space-y-2 text-xs font-mono text-[#e4e4e7]">
                                                    <li className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-blue-500"></span><strong>Frontend:</strong> React, TypeScript, Tailwind</li>
                                                    <li className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-yellow-500"></span><strong>Backend:</strong> Python (Flask), SQLite, Socket.IO</li>
                                                    <li className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-green-500"></span><strong>Core:</strong> LTFS, SG3_Utils, Streaming Logic</li>
                                                </ul>
                                            </div>
                                            <div>
                                                <h4 className="text-white font-bold uppercase text-xs mb-4">Optimized Data Flow</h4>
                                                <div className="text-xs font-mono text-[#71717a] space-y-2">
                                                    Source (SMB/NFS/S3) <br />
                                                    &darr; <br />
                                                    NVMe Staging (IOStat Analytics) <br />
                                                    &darr; <br />
                                                    LTO Tape (Multi-Consumer Pipeline)
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </section>

                                <section>
                                    <h3 className="text-white text-2xl font-bold uppercase tracking-wide mb-6 flex items-center gap-2">
                                        <span className="material-symbols-outlined text-primary">psychology</span>
                                        Philosophy of Operation
                                    </h3>
                                    <div className="text-sm text-[#a1a1aa] leading-relaxed space-y-4">
                                        <p>
                                            <strong>1. Air Gaps are Physical:</strong> FossilSafe encourages ejecting tapes. A tape in a library is "Offline" but not "Air Gapped". A tape on a shelf is Air Gapped.
                                        </p>
                                        <p>
                                            <strong>2. Self-Describing Media:</strong> We strictly use LTFS. You can take a FossilSafe tape, put it in ANY Linux/Mac/Windows machine with an LTO drive, and read files without our software. We do not use proprietary tarballs or database-dependent formats for the data itself.
                                        </p>
                                        <p>
                                            <strong>3. The Catalog is a Cache:</strong> The SQLite database is merely a searchable index of what IS on your tapes. If the database dies, the tapes still hold the truth.
                                        </p>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'install' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">Hardware & Environment</h3>
                                    <div className="space-y-6">
                                        <div>
                                            <h4 className="text-white font-bold uppercase mb-2">Supported Hardware</h4>
                                            <ul className="list-disc list-inside text-sm text-[#a1a1aa] space-y-1">
                                                <li><strong>Drives:</strong> IBM, HP, Quantum LTO-5, 6, 7, 8, 9. (SAS or Fibre Channel)</li>
                                                <li><strong>Libraries:</strong> Tape libraries with standard SCSI robotics (e.g., TL2000, MSL2024).</li>
                                                <li><strong>Host HBA:</strong> LSI SAS 9200/9300 series (IT Mode recommended).</li>
                                            </ul>
                                        </div>

                                        <div className="p-4 bg-warning/10 border border-warning/20 rounded">
                                            <strong className="text-warning block mb-1 text-xs uppercase">Passthrough Warning</strong>
                                            <p className="text-xs text-[#e4e4e7]">
                                                If running in a VM (ESXi/Proxmox), you MUST pass through the SAS Controller (PCIe Passthrough).
                                                USB redirection of tape drives is <strong>extremely unreliable</strong> and will cause I/O errors.
                                            </p>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'concepts' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">Tape Logic</h3>
                                    <div className="grid grid-cols-2 gap-8">
                                        <div>
                                            <h4 className="text-primary font-bold uppercase mb-3">LTO Generations</h4>
                                            <table className="w-full text-xs text-[#a1a1aa] border-collapse">
                                                <thead>
                                                    <tr className="border-b border-[#27272a]">
                                                        <th className="py-2 text-left">Gen</th>
                                                        <th className="py-2 text-left">Raw Cap</th>
                                                        <th className="py-2 text-left">Read/Write</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                                    <tr className="border-b border-[#27272a]/20"><td>LTO-5</td><td>1.5 TB</td><td>LTO-5, 4(R)</td></tr>
                                                    <tr className="border-b border-[#27272a]/20"><td>LTO-6</td><td>2.5 TB</td><td>LTO-6, 5</td></tr>
                                                    <tr className="border-b border-[#27272a]/20"><td>LTO-7</td><td>6.0 TB</td><td>LTO-7, 6</td></tr>
                                                    <tr className="border-b border-[#27272a]/20"><td>LTO-8</td><td>12.0 TB</td><td>LTO-8, 7</td></tr>
                                                </tbody>
                                            </table>
                                        </div>
                                        <div>
                                            <h4 className="text-primary font-bold uppercase mb-3">Tape States</h4>
                                            <ul className="space-y-3 text-xs text-[#e4e4e7]">
                                                <li><strong className="text-white bg-[#27272a] px-1 rounded">BLANK</strong>: Raw tape. Needs <code>mkltfs</code>.</li>
                                                <li><strong className="text-white bg-[#27272a] px-1 rounded">APPENDABLE</strong>: Valid LTFS. Has space.</li>
                                                <li><strong className="text-white bg-[#27272a] px-1 rounded">FULL</strong>: Marked as read-only by FossilSafe logic or physically locked.</li>
                                            </ul>
                                        </div>
                                    </div>
                                </section>

                                <section>
                                    <h3 className="text-white text-2xl font-bold uppercase tracking-wide mb-6">Job Lifecycle</h3>
                                    <div className="relative border-l-2 border-[#27272a] pl-8 space-y-8">
                                        <div className="relative">
                                            <div className="absolute -left-[41px] top-0 w-5 h-5 rounded-full bg-[#27272a] border-2 border-[#71717a]"></div>
                                            <h4 className="text-white font-bold uppercase text-sm">1. Queued</h4>
                                            <p className="text-xs text-[#71717a] mt-1">Job sits in database waiting for the scheduler.</p>
                                        </div>
                                        <div className="relative">
                                            <div className="absolute -left-[41px] top-0 w-5 h-5 rounded-full bg-primary border-2 border-primary animate-pulse"></div>
                                            <h4 className="text-white font-bold uppercase text-sm">2. Running / Spanning</h4>
                                            <p className="text-xs text-[#71717a] mt-1">
                                                Data streams to tape. If tape fills, system pauses, unloads, and requests next tape (Email/Barcode alert).
                                                This is "Tape Spanning".
                                            </p>
                                        </div>
                                        <div className="relative">
                                            <div className="absolute -left-[41px] top-0 w-5 h-5 rounded-full bg-[#27272a] border-2 border-[#71717a]"></div>
                                            <h4 className="text-white font-bold uppercase text-sm">3. Verification (Optional)</h4>
                                            <p className="text-xs text-[#71717a] mt-1">System rewinds and reads back checksums (xxHash) to ensure integrity.</p>
                                        </div>
                                        <div className="relative">
                                            <div className="absolute -left-[41px] top-0 w-5 h-5 rounded-full bg-green-500 border-2 border-green-500"></div>
                                            <h4 className="text-white font-bold uppercase text-sm">4. Completed</h4>
                                            <p className="text-xs text-[#71717a] mt-1">File index is committed to the "FossilSafe Catalog".</p>
                                        </div>
                                    </div>

                                    <div className="p-4 bg-[#38bdf8]/5 border border-[#38bdf8]/20 rounded mt-8">
                                        <h4 className="text-[#38bdf8] font-bold uppercase text-xs mb-2">Streaming Engine (Epic 6)</h4>
                                        <p className="text-xs text-[#a1a1aa] leading-relaxed mb-4">
                                            Modern tape drives require a constant flow of data to prevent "shoe-shining" (frequent stopping and repositioning of the tape).
                                            FossilSafe uses a multi-threaded staging pipeline to maintain native wire-speeds.
                                        </p>
                                        <ul className="text-[11px] text-[#71717a] space-y-2 font-mono">
                                            <li><strong className="text-white">STAGE-AHEAD:</strong> The system buffers data on NVMe storage before starting the tape write.</li>
                                            <li><strong className="text-white">IOStatTracker:</strong> High-precision telemetry monitors "Feeder" (disk) vs "Ingest" (tape) rates.</li>
                                            <li><strong className="text-white">PREFLIGHT:</strong> Pre-job validation and multi-tape spanning forecasting.</li>
                                            <li><strong className="text-white">PARALLELISM:</strong> Supports streaming to multiple drives concurrently.</li>
                                        </ul>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'hardware' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">Hardware Management</h3>
                                    <div className="text-sm text-[#a1a1aa] mb-6">
                                        Use the <strong>Equipment</strong> page to manage physical devices.
                                    </div>

                                    <div className="space-y-8">
                                        <div>
                                            <h4 className="text-primary font-bold uppercase mb-2">Drives (Physical_Drives)</h4>
                                            <p className="text-xs text-[#71717a] mb-3">Direct control over SAS/FC tape drives.</p>
                                            <ul className="list-disc list-inside text-xs text-[#a1a1aa] space-y-2 font-mono">
                                                <li><strong>Status:</strong> Online (Idle), Busy (Writing/Reading), Offline (Error).</li>
                                                <li><strong>Unmount:</strong> Forces the drive to rewind and eject the tape. If in a library, the arm will move it to a slot.</li>
                                                <li><strong>Speed:</strong> Real-time throughput (MB/s).</li>
                                            </ul>
                                        </div>

                                        <div>
                                            <h4 className="text-primary font-bold uppercase mb-2">Libraries (Media_Changers)</h4>
                                            <p className="text-xs text-[#71717a] mb-3">Robotic tape libraries (Autoloaders).</p>
                                            <div className="bg-[#121214] p-4 rounded border border-[#27272a] space-y-4">
                                                <div>
                                                    <strong className="text-white text-xs block mb-2">Standard Actions:</strong>
                                                    <ul className="text-xs text-[#a1a1aa] space-y-2">
                                                        <li><strong>Re-Scan Bus:</strong> Forces Linux SCSI bus rescan. Use this after powering on a drive.</li>
                                                        <li><strong>Library Selector:</strong> In multi-library environments, use the dropdown in the header to switch between different robotic changers.</li>
                                                        <li><strong>Inventory Browse:</strong> View which barcode is in which slot.</li>
                                                        <li><strong>Move Command:</strong> Manually command the robot to move tape from Slot X to Drive Y.</li>
                                                    </ul>
                                                </div>

                                                <div className="pt-4 border-t border-[#27272a]">
                                                    <strong className="text-primary text-xs block mb-2 font-black uppercase tracking-widest">Interactive Calibration:</strong>
                                                    <p className="text-[10px] text-[#71717a] mb-3">
                                                        Operating systems rarely assign <code>/dev/nst</code> nodes in the same order as physical slots.
                                                        FossilSafe uses a <strong>Mechanical Calibration Wizard</strong> to solve this:
                                                    </p>
                                                    <ul className="text-[10px] text-[#a1a1aa] space-y-1 italic">
                                                        <li>1. Click "Calibrate Drive Map" in the Equipment view.</li>
                                                        <li>2. The system loads a verified tape into each physical drive.</li>
                                                        <li>3. It scans all logical paths to find the tape's MAM barcode.</li>
                                                        <li>4. The result is saved to persistent storage.</li>
                                                    </ul>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </section>

                                <section>
                                    <h3 className="text-white text-2xl font-bold uppercase tracking-wide mb-6">Tape Inventory</h3>
                                    <div className="space-y-4">
                                        <p className="text-sm text-[#a1a1aa]">
                                            The <strong>Tapes</strong> page tracks every cassette ever seen.
                                        </p>
                                        <div className="grid grid-cols-3 gap-2 text-xs font-mono text-[#e4e4e7]">
                                            <div className="bg-[#121214] p-3 rounded border border-[#27272a]">
                                                <strong className="text-primary block mb-1">Format (MKLTFS)</strong>
                                                Destructive. Writes LTFS headers. Required for new tapes. The <strong>Setup Wizard</strong> can perform this in bulk for all tapes in a library.
                                            </div>
                                            <div className="bg-[#121214] p-3 rounded border border-[#27272a]">
                                                <strong className="text-destructive block mb-1">Wipe</strong>
                                                Secure Erase. Overwrites data with zeros/random patterns.
                                            </div>
                                            <div className="bg-[#121214] p-3 rounded border border-[#27272a]">
                                                <strong className="text-white block mb-1">Eject/Load</strong>
                                                Moves tape between Slot and Drive.
                                            </div>
                                        </div>
                                        <div className="p-4 bg-primary/5 border border-primary/20 rounded space-y-4 mt-8">
                                            <div>
                                                <h4 className="text-primary font-bold uppercase text-xs mb-2">LTO Hardware Encryption</h4>
                                                <p className="text-xs text-[#a1a1aa] leading-relaxed">
                                                    FossilSafe supports native LTO drive encryption. Hardware encryption happens at the drive's line speed and provides zero performance overhead.
                                                    The keys are managed by integration with existing KMS providers.
                                                </p>
                                            </div>

                                            <div className="pt-4 border-t border-primary/10">
                                                <h4 className="text-primary font-bold uppercase text-xs mb-2">Predictive Health (TapeLink)</h4>
                                                <p className="text-xs text-[#a1a1aa] leading-relaxed">
                                                    FossilSafe monitors LTO <strong>TapeAlerts</strong> (SCSI Log Page 0x2E). This allows the system to identify impending media degradation or drive failure before data is lost.
                                                </p>
                                                <p className="text-[10px] text-[#71717a] mt-2 italic">
                                                    View health logs by clicking the "Health" indicator in the drive topology or the "Hardware Health" tab in the tape details panel.
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'jobs' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">Automation & Scheduling</h3>

                                    <div className="space-y-8">
                                        <div>
                                            <h4 className="text-primary font-bold uppercase mb-2">Schedules</h4>
                                            <p className="text-xs text-[#a1a1aa] mb-2">
                                                Automate recurring backups using standard Cron syntax. FossilSafe supports full CRUD (Create, Read, Update, Delete) operations for schedules.
                                            </p>
                                            <div className="bg-[#121214] p-4 rounded border border-[#27272a] text-xs font-mono text-[#e4e4e7]">
                                                <p className="mb-2"><strong>Syntax:</strong> <code>Min Hour Day Month DayOfWeek</code></p>
                                                <p>Example: <code>0 2 * * *</code> = Every day at 2:00 AM.</p>
                                                <p>Example: <code>0 20 * * 5</code> = Every Friday at 8:00 PM.</p>
                                            </div>
                                            <div className="mt-2 text-[10px] text-[#71717a] italic">
                                                Use the <strong>Edit</strong> button in the Schedules page to modify existing jobs without recreating them.
                                            </div>
                                        </div>

                                        <div>
                                            <h4 className="text-primary font-bold uppercase mb-2">Job Management</h4>
                                            <ul className="list-disc list-inside text-xs text-[#a1a1aa] space-y-1">
                                                <li><strong>Active Jobs:</strong> View real-time progress, current file, and speed.</li>
                                                <li><strong>History:</strong> View logs of past jobs. Click a job ID to see the full `stdout` log.</li>
                                                <li><strong>Consistency Hooks:</strong> Execute custom pre/post scripts.</li>
                                                <li><strong>Cancellation:</strong> Send `SIGTERM` to the underlying process. Safe but may leave a tape in "Recovery Needed" state (requires mount to fix).</li>
                                            </ul>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'admin' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">Administration & Settings</h3>

                                    <div className="space-y-8">
                                        <div>
                                            <h4 className="text-white font-bold uppercase mb-4">User Management</h4>
                                            <div className="grid grid-cols-2 gap-4">
                                                <div className="bg-[#121214] p-3 rounded border border-[#27272a]">
                                                    <strong className="text-primary text-xs uppercase block mb-1">Roles</strong>
                                                    <p className="text-[10px] text-[#71717a]">
                                                        <strong>Admin:</strong> Full access.<br />
                                                        <strong>Operator:</strong> Cannot change settings or delete data.
                                                    </p>
                                                </div>
                                                <div className="bg-[#121214] p-3 rounded border border-[#27272a]">
                                                    <strong className="text-primary text-xs uppercase block mb-1">Security</strong>
                                                    <p className="text-[10px] text-[#71717a]">
                                                        Supports 2FA (TOTP). Admins can reset 2FA for users in the "Users" tab.
                                                    </p>
                                                </div>
                                            </div>
                                        </div>

                                        <div>
                                            <h4 className="text-white font-bold uppercase mb-4">Job Wizard</h4>
                                            <ul className="list-disc list-inside text-xs text-[#a1a1aa] space-y-1">
                                                <li><strong>Sources:</strong> Identify the directories or network shares to back up.</li>
                                                <li><strong>Tapes:</strong> Select available media. The system will calculate if enough space is available.</li>
                                                <li><strong>Preflight:</strong> A final sanity check of connections and permissions. Includes a <strong>Spanning Forecast</strong> showing the exact file-to-tape layout.</li>
                                                <li><strong>Execution:</strong> Start the job. The system transitions to the "Dashboard" for real-time tracking.</li>
                                            </ul>
                                        </div>

                                        <div>
                                            <h4 className="text-white font-bold uppercase mb-4">Notifications</h4>
                                            <ul className="list-disc list-inside text-xs text-[#a1a1aa] space-y-1 mb-4">
                                                <li><strong>SMTP:</strong> Standard email alerts.</li>
                                                <li><strong>Webhooks:</strong> Real-time Slack/Discord integration with HMAC-SHA256 signing.</li>
                                                <li><strong>Compliance Alarms:</strong> Immediate notification of WORM violations or audit tampering.</li>
                                                <li><strong>SNMP:</strong> For NOC integration.</li>
                                            </ul>
                                        </div>

                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'cli' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">CLI Master Reference</h3>

                                    <div className="space-y-6">
                                        <p className="text-sm text-[#a1a1aa]">
                                            The <code>fsafe-cli</code> command is a unified wrapper around the FossilSafe API. It provides professional systems management and scripting capabilities.
                                        </p>

                                        <div className="bg-[#0c0c0e] border border-[#27272a] rounded p-4 font-mono text-xs overflow-x-auto">
                                            <p className="text-[#71717a] mb-2"># 1. Check system health and connection</p>
                                            <p className="text-primary mb-4">$ fsafe-cli system health</p>

                                            <p className="text-[#71717a] mb-2"># 2. View hardware diagnostic history</p>
                                            <p className="text-primary mb-4">$ fsafe-cli system diag history</p>

                                            <p className="text-[#71717a] mb-2"># 3. Rotate local master encryption key</p>
                                            <p className="text-primary mb-4">$ fsafe-cli kms rotate</p>

                                            <p className="text-[#71717a] mb-2"># 4. Trigger cryptographic audit verification</p>
                                            <p className="text-primary mb-4">$ fsafe-cli audit verify</p>

                                            <p className="text-[#71717a] mb-2"># 5. List available tape libraries</p>
                                            <p className="text-primary mb-4">$ fsafe-cli system libraries</p>

                                            <p className="text-[#71717a] mb-2"># 6. Archive a folder to tape (Job creation)</p>
                                            <p className="text-primary mb-4">$ fsafe-cli job create --name "Manual" --source-id "NAS1" --path "/docs"</p>

                                            <p className="text-[#71717a] mb-2"># 7. List active tape jobs</p>
                                            <p className="text-primary mb-4">$ fsafe-cli job list</p>

                                            <p className="text-[#71717a] mb-2"># 8. Unmount tape from drive 0</p>
                                            <p className="text-primary">$ fsafe-cli tape unmount 0</p>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'recovery' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-2xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-2">Disaster Recovery (DR)</h3>

                                    <div className="p-4 border border-destructive/30 bg-destructive/5 rounded flex gap-4 items-start mb-6">
                                        <span className="material-symbols-outlined text-destructive text-3xl shrink-0">warning</span>
                                        <div>
                                            <h4 className="text-white font-bold uppercase tracking-wide mb-1">Emergency Protocol</h4>
                                            <p className="text-sm text-[#a1a1aa] font-mono">
                                                If the FossilSafe database is corrupted or lost, everything can still be recovered.
                                                Every tape is self-describing (LTFS).
                                            </p>
                                        </div>
                                    </div>

                                    <div className="space-y-6">
                                        <div>
                                            <h4 className="text-white font-bold uppercase mb-2">1. Catalog Rebuild</h4>
                                            <p className="text-sm text-[#a1a1aa] mb-2">
                                                Use this if you have lost your database but have a fresh FossilSafe install.
                                            </p>
                                            <div className="bg-[#121214] p-3 rounded border border-[#27272a] text-xs font-mono text-[#e4e4e7]">
                                                Go to Recovery &rarr; Rebuild Catalog. <br />
                                                Insert tapes. System scans LTFS indices and rebuilds the file map.
                                            </div>
                                        </div>

                                        <div>
                                            <h4 className="text-destructive font-bold uppercase mb-2">2. Emergency Dump (Last Resort)</h4>
                                            <p className="text-sm text-[#a1a1aa] mb-2">
                                                Bypasses the database entirely.
                                            </p>
                                            <div className="bg-[#121214] p-3 rounded border border-[#27272a] text-xs font-mono text-[#e4e4e7]">
                                                Go to Recovery &rarr; Emergency Dump. <br />
                                                Performs a raw `tar` or file copy of the entire tape to local disk.
                                            </div>
                                        </div>

                                        <div className="p-4 bg-green-500/5 border border-green-500/20 rounded">
                                            <h4 className="text-green-500 font-bold uppercase text-xs mb-2">Internal Restore Folder (Recommended)</h4>
                                            <p className="text-xs text-[#a1a1aa] leading-relaxed">
                                                FossilSafe provides a high-performance restore target at <code>/var/lib/fossilsafe/restore</code>.
                                                This folder is specifically whitelisted in the security engine and is the recommended destination for temporary file retrievals.
                                            </p>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}

                        {activeTab === 'troubleshoot' && (
                            <div className="space-y-12 animate-in fade-in slide-in-from-bottom-4 duration-500">
                                <section>
                                    <h3 className="text-white text-3xl font-bold uppercase tracking-wide mb-6 border-b border-[#27272a] pb-4">Diagnostic Knowledge Base</h3>

                                    <div className="space-y-6">
                                        <div className="grid grid-cols-[120px_1fr] gap-6 p-4 border-b border-[#27272a]">
                                            <div className="text-destructive font-bold font-mono text-xs">SCSI_ERR_NO_DEVICE</div>
                                            <div>
                                                <strong className="text-white text-xs uppercase block mb-1">Hardware not found</strong>
                                                <p className="text-xs text-[#a1a1aa] mb-2">Backend cannot see <code>/dev/sg*</code> or <code>/dev/nst*</code>.</p>
                                                <ul className="list-disc list-inside text-xs text-[#71717a]">
                                                    <li>Check SAS cable connection.</li>
                                                    <li>Check host HBA drivers.</li>
                                                    <li>If VM: verify PCI Passthrough (VT-d/AMD-Vi) is active.</li>
                                                </ul>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-[120px_1fr] gap-6 p-4 border-b border-[#27272a]">
                                            <div className="text-destructive font-bold font-mono text-xs">LTFS_MOUNT_FAIL</div>
                                            <div>
                                                <strong className="text-white text-xs uppercase block mb-1">Tape Format Issue</strong>
                                                <p className="text-xs text-[#a1a1aa] mb-2">The tape is not a valid LTFS volume or is dirty.</p>
                                                <p className="text-xs text-[#71717a]">Run <code>ltfsck /dev/st0</code> in the terminal to repair the consistency check.</p>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-[120px_1fr] gap-6 p-4 border-b border-[#27272a]">
                                            <div className="text-destructive font-bold font-mono text-xs">JOB_STALLED</div>
                                            <div>
                                                <strong className="text-white text-xs uppercase block mb-1">Source Too Slow</strong>
                                                <p className="text-xs text-[#a1a1aa] mb-2">Buffer underrun. Tape drive is "shoe-shining" (stopping/starting).</p>
                                                <p className="text-xs text-[#71717a] mb-2">Monitor the <strong>Staging Health Widget</strong> on the dashboard. If "Underrun Risk" is displayed, improve network throughput or increase the staging buffer size.</p>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-[120px_1fr] gap-6 p-4 border-b border-[#27272a]">
                                            <div className="text-warning font-bold font-mono text-xs">MTX_MAP_MISMATCH</div>
                                            <div>
                                                <strong className="text-white text-xs uppercase block mb-1">Logical/Physical Mismatch</strong>
                                                <p className="text-xs text-[#a1a1aa] mb-2">The system is trying to write to Drive 0, but the OS has labeled that physical hardware as <code>/dev/nst1</code>.</p>
                                                <p className="text-xs text-[#71717a]">Run the <strong>Drive Calibration Wizard</strong> in the Equipment tab to synchronize the mapping.</p>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-[120px_1fr] gap-6 p-4 border-b border-[#27272a]">
                                            <div className="text-primary font-bold font-mono text-xs">SUPPORT_BUNDLE</div>
                                            <div>
                                                <strong className="text-white text-xs uppercase block mb-1">System Reliability</strong>
                                                <p className="text-xs text-[#a1a1aa] mb-2">If you experience persistent issues, use the <strong>Support Bundle</strong> feature in <strong>Settings &gt; Diagnostics</strong>.</p>
                                                <p className="text-xs text-[#71717a]">You can also view <strong>Diagnostic History</strong> and <strong>Audit Verification History</strong> to track system reliability over time.</p>
                                            </div>
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    )
}
