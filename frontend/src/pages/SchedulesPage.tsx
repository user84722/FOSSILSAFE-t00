import { useState, useEffect } from "react"
import { api } from "@/lib/api"
import { useDemoMode } from "@/lib/demoMode"
import CreateScheduleModal from "@/components/schedules/CreateScheduleModal"
import EditScheduleModal from "@/components/schedules/EditScheduleModal"
import { ErrorState } from "@/components/ui/ErrorState"
import ConfirmationModal from "@/components/ui/ConfirmationModal"

export default function SchedulesPage() {
    const { isDemoMode } = useDemoMode()
    const [schedules, setSchedules] = useState<any[]>([])
    const [isLoading, setIsLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [showCreateModal, setShowCreateModal] = useState(false)
    const [editSchedule, setEditSchedule] = useState<{ isOpen: boolean; data: any }>({
        isOpen: false,
        data: null
    })

    const [confirmModal, setConfirmModal] = useState<{
        isOpen: boolean;
        id: string | null;
    }>({
        isOpen: false,
        id: null
    })

    const fetchSchedules = async () => {
        setIsLoading(true)
        setError(null)
        if (isDemoMode) {
            setSchedules([
                { id: '1', name: 'Daily Backup', cron: '0 2 * * *', job_type: 'backup', enabled: true, last_run: '2023-10-24 02:00', next_run: '2023-10-25 02:00', source_id: 1, compression: 'zstd' },
                { id: '2', name: 'Weekly Archive', cron: '0 4 * * 0', job_type: 'archive', enabled: true, last_run: '2023-10-22 04:00', next_run: '2023-10-29 04:00', source_id: 2, compression: 'lz4' }
            ])
            setIsLoading(false)
            return
        }

        try {
            const res = await api.getSchedules()
            if (res.success && res.data) {
                setSchedules(res.data.schedules)
            } else {
                setError(res.error || 'Failed to fetch schedules')
            }
        } catch (err) {
            console.error(err)
            setError('Network error or backend unreachable')
        } finally {
            setIsLoading(false)
        }
    }

    useEffect(() => {
        fetchSchedules()
    }, [isDemoMode])

    const handleDelete = async (id: string) => {
        if (isDemoMode) {
            setSchedules(prev => prev.filter(s => s.id !== id))
            return
        }

        const res = await api.deleteSchedule(id)
        if (res.success) {
            fetchSchedules()
        }
    }


    return (
        <div className="flex flex-col gap-8 h-full">
            <header className="flex items-center justify-between shrink-0">
                <div>
                    <h1 className="text-xl font-bold tracking-tight text-white uppercase antialiased">Schedules</h1>
                    <p className="text-xs text-[#71717a] font-mono mt-1">Automated job execution</p>
                </div>
                    <button
                        onClick={() => setShowCreateModal(true)}
                        className="px-4 py-2 text-xs font-bold uppercase tracking-wider rounded transition-all flex items-center gap-2 bg-primary hover:bg-green-400 text-black shadow-[0_4px_12px_rgba(25,230,100,0.2)]"
                    >
                        <span className="material-symbols-outlined text-sm">add</span>
                        Create Schedule
                    </button>
            </header>

            <div className="flex-1 min-h-0 bg-[#121214] border border-[#27272a] rounded-lg overflow-hidden flex flex-col">
                <div className="overflow-auto flex-1 custom-scrollbar">
                    <table className="w-full text-left text-xs">
                        <thead className="bg-[#18181b] text-[9px] font-bold uppercase tracking-widest text-[#71717a] border-b border-[#27272a] sticky top-0 z-10">
                            <tr>
                                <th className="px-6 py-4">Name</th>
                                <th className="px-6 py-4">Schedule (Cron)</th>
                                <th className="px-6 py-4">Type</th>
                                <th className="px-6 py-4">Next Run</th>
                                <th className="px-6 py-4">Status</th>
                                <th className="px-6 py-4 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-[#27272a]">
                            {isLoading ? (
                                <tr><td colSpan={6} className="px-6 py-12 text-center text-[#71717a]">Loading schedules...</td></tr>
                            ) : error ? (
                                <tr>
                                    <td colSpan={6} className="p-4">
                                        <ErrorState
                                            title="Failed to Load Schedules"
                                            message={error}
                                            retry={fetchSchedules}
                                            retryLabel="Retry"
                                            className="h-64 border-none bg-transparent"
                                        />
                                    </td>
                                </tr>
                            ) : schedules.length === 0 ? (
                                <tr><td colSpan={6} className="px-6 py-12 text-center text-[#71717a]">No schedules configured</td></tr>
                            ) : schedules.map((schedule) => (
                                <tr key={schedule.id} className="hover:bg-white/[0.02] transition-colors group">
                                    <td className="px-6 py-4 font-bold text-white">{schedule.name}</td>
                                    <td className="px-6 py-4 font-mono text-[#71717a]">{schedule.cron}</td>
                                    <td className="px-6 py-4">
                                        <span className="px-2 py-1 rounded bg-[#27272a] border border-[#3f3f46] text-[10px] uppercase font-bold text-[#a1a1aa]">{schedule.job_type}</span>
                                    </td>
                                    <td className="px-6 py-4 font-mono text-primary">{schedule.next_run || '-'}</td>
                                    <td className="px-6 py-4">
                                        <span className={`px-2 py-1 rounded text-[9px] font-bold uppercase tracking-widest ${schedule.enabled ? 'bg-primary/10 text-primary border border-primary/20' : 'bg-[#27272a] text-[#71717a]'}`}>
                                            {schedule.enabled ? 'Active' : 'Disabled'}
                                        </span>
                                    </td>
                                    <td className="px-6 py-4 text-right">
                                        <div className="flex justify-end gap-2">
                                            <button
                                                onClick={() => setEditSchedule({ isOpen: true, data: schedule })}
                                                className="text-[#71717a] hover:text-primary transition-colors opacity-0 group-hover:opacity-100"
                                                title="Edit Schedule"
                                            >
                                                <span className="material-symbols-outlined text-lg">edit</span>
                                            </button>
                                            <button
                                                onClick={() => setConfirmModal({ isOpen: true, id: schedule.id })}
                                                className="text-[#71717a] hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                                                title="Delete Schedule"
                                            >
                                                <span className="material-symbols-outlined text-lg">delete</span>
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            <CreateScheduleModal
                isOpen={showCreateModal}
                onClose={() => setShowCreateModal(false)}
                onSuccess={fetchSchedules}
            />

            <EditScheduleModal
                isOpen={editSchedule.isOpen}
                onClose={() => setEditSchedule({ isOpen: false, data: null })}
                onSuccess={fetchSchedules}
                schedule={editSchedule.data}
            />

            <ConfirmationModal
                isOpen={confirmModal.isOpen}
                onClose={() => setConfirmModal({ isOpen: false, id: null })}
                onConfirm={() => confirmModal.id && handleDelete(confirmModal.id)}
                title="Delete Schedule"
                message="Are you sure you want to delete this schedule? This action cannot be undone."
                variant="danger"
                confirmText="Delete Schedule"
            />
        </div>
    )
}


