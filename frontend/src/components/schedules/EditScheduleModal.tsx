import { useState, useEffect } from "react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useDemoMode } from "@/lib/demoMode"

interface EditScheduleModalProps {
    isOpen: boolean
    onClose: () => void
    onSuccess: () => void
    schedule: any
}

type Frequency = 'daily' | 'weekly' | 'custom'

export default function EditScheduleModal({ isOpen, onClose, onSuccess, schedule }: EditScheduleModalProps) {
    const { isDemoMode } = useDemoMode()
    const [name, setName] = useState('')
    const [jobType, setJobType] = useState('backup')
    const [sources, setSources] = useState<any[]>([])
    const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null)
    const [compression, setCompression] = useState('zstd')
    const [frequency, setFrequency] = useState<Frequency>('daily')
    const [time, setTime] = useState('02:00')
    const [dayOfWeek, setDayOfWeek] = useState('mon')
    const [customCron, setCustomCron] = useState('0 2 * * *')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [error, setError] = useState('')

    useEffect(() => {
        if (isOpen) {
            fetchSources()
            if (schedule) {
                setName(schedule.name || '')
                setJobType(schedule.job_type || 'backup')
                setSelectedSourceId(schedule.source_id || null)
                setCompression(schedule.compression || 'zstd')
                parseCron(schedule.cron)
            }
        }
    }, [isOpen, schedule])

    const parseCron = (cron: string) => {
        if (!cron) return
        setCustomCron(cron)
        
        const parts = cron.split(' ')
        if (parts.length === 5) {
            const [m, h, dom, mon, dow] = parts
            if (dom === '*' && mon === '*') {
                if (dow === '*') {
                    setFrequency('daily')
                    setTime(`${h.padStart(2, '0')}:${m.padStart(2, '0')}`)
                } else if (dow.length === 3) {
                    setFrequency('weekly')
                    setDayOfWeek(dow.toLowerCase())
                    setTime(`${h.padStart(2, '0')}:${m.padStart(2, '0')}`)
                } else {
                    setFrequency('custom')
                }
            } else {
                setFrequency('custom')
            }
        } else {
            setFrequency('custom')
        }
    }

    const fetchSources = async () => {
        if (isDemoMode) {
            setSources([
                { id: 1, name: 'Production Share', type: 'smb', config: { path: '//nas-01/production' } },
                { id: 2, name: 'Media Assets', type: 's3', config: { bucket: 'corporate-media-assets' } }
            ])
            return
        }
        try {
            const res = await api.getSources()
            if (res.success && res.data) {
                setSources(res.data.sources)
            }
        } catch (err) {
            console.error("Failed to fetch sources", err)
        }
    }

    const getCronExpression = () => {
        if (frequency === 'custom') return customCron

        const [hour, minute] = time.split(':')
        const m = parseInt(minute) || 0
        const h = parseInt(hour) || 0

        if (frequency === 'daily') {
            return `${m} ${h} * * *`
        } else if (frequency === 'weekly') {
            return `${m} ${h} * * ${dayOfWeek}`
        }
        return '* * * * *'
    }

    const handleSubmit = async () => {
        if (!name || !selectedSourceId) {
            setError('Please fill in all required fields')
            return
        }

        setIsSubmitting(true)
        setError('')

        try {
            const cron = getCronExpression()
            const scheduleData = {
                name,
                cron,
                job_type: jobType,
                source_id: selectedSourceId,
                compression: compression,
            }

            if (isDemoMode) {
                setTimeout(() => {
                    onSuccess()
                    onClose()
                }, 1000)
            } else {
                const res = await api.updateSchedule(schedule.id, scheduleData)
                if (res.success) {
                    onSuccess()
                    onClose()
                } else {
                    setError(res.error || 'Failed to update schedule')
                }
            }
        } catch (err) {
            setError('Network error')
        } finally {
            setIsSubmitting(false)
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
            <div className="relative w-full max-w-lg bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden">
                <div className="flex items-center justify-between px-6 py-4 border-b border-[#27272a]">
                    <h2 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                        <span className="material-symbols-outlined text-primary">edit_calendar</span>
                        Edit Schedule
                    </h2>
                    <button onClick={onClose} className="text-[#71717a] hover:text-white">
                        <span className="material-symbols-outlined">close</span>
                    </button>
                </div>

                <div className="p-6 space-y-4">
                    {error && (
                        <div className="p-3 bg-destructive/10 border border-destructive/20 rounded text-destructive text-xs">
                            {error}
                        </div>
                    )}

                    <div className="space-y-2">
                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Schedule Name</label>
                        <input
                            type="text"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="Daily Backup"
                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                        />
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Job Type</label>
                            <select
                                value={jobType}
                                onChange={(e) => setJobType(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                            >
                                <option value="backup">Backup (Keep Source)</option>
                                <option value="archive">Archive (Move to Tape)</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Source</label>
                            <select
                                value={selectedSourceId || ''}
                                onChange={(e) => setSelectedSourceId(parseInt(e.target.value))}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                            >
                                <option value="" disabled>Select a source</option>
                                {sources.map(s => (
                                    <option key={s.id} value={s.id}>{s.name} ({s.type})</option>
                                ))}
                            </select>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Compression</label>
                        <select
                            value={compression}
                            onChange={(e) => setCompression(e.target.value)}
                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                        >
                            <option value="zstd">Zstandard (High Performance)</option>
                            <option value="gzip">Gzip (Standard)</option>
                            <option value="lz4">LZ4 (Fastest)</option>
                            <option value="none">None (Disabled)</option>
                        </select>
                    </div>

                    <div className="border-t border-[#27272a] pt-4 mt-2">
                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest mb-3 block">Frequency</label>
                        <div className="flex gap-2 mb-4">
                            {(['daily', 'weekly', 'custom'] as Frequency[]).map(f => (
                                <button
                                    key={f}
                                    onClick={() => setFrequency(f)}
                                    className={cn(
                                        "flex-1 py-2 text-xs font-bold uppercase tracking-wider rounded border transition-all",
                                        frequency === f
                                            ? "bg-primary/10 border-primary text-primary"
                                            : "bg-[#18181b] border-[#27272a] text-[#71717a] hover:bg-[#27272a]"
                                    )}
                                >
                                    {f}
                                </button>
                            ))}
                        </div>

                        {frequency === 'daily' && (
                            <div className="space-y-2">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Run At Time</label>
                                <input
                                    type="time"
                                    value={time}
                                    onChange={(e) => setTime(e.target.value)}
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                                />
                            </div>
                        )}

                        {frequency === 'weekly' && (
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Day of Week</label>
                                    <select
                                        value={dayOfWeek}
                                        onChange={(e) => setDayOfWeek(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                                    >
                                        <option value="mon">Monday</option>
                                        <option value="tue">Tuesday</option>
                                        <option value="wed">Wednesday</option>
                                        <option value="thu">Thursday</option>
                                        <option value="fri">Friday</option>
                                        <option value="sat">Saturday</option>
                                        <option value="sun">Sunday</option>
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Run At Time</label>
                                    <input
                                        type="time"
                                        value={time}
                                        onChange={(e) => setTime(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm text-white focus:border-primary/50 outline-none"
                                    />
                                </div>
                            </div>
                        )}

                        {frequency === 'custom' && (
                            <div className="space-y-2">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest">Cron Expression</label>
                                <input
                                    type="text"
                                    value={customCron}
                                    onChange={(e) => setCustomCron(e.target.value)}
                                    placeholder="* * * * *"
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm font-mono text-white focus:border-primary/50 outline-none"
                                />
                                <p className="text-[10px] text-[#71717a]">Format: min hour dom mon dow</p>
                            </div>
                        )}
                    </div>
                </div>

                <div className="px-6 py-4 border-t border-[#27272a] bg-[#09090b] flex justify-end gap-2">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-[#71717a] hover:text-white text-xs font-bold uppercase tracking-wider transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        disabled={isSubmitting}
                        className={cn(
                            "px-6 py-2 bg-primary hover:bg-green-400 text-black text-xs font-bold uppercase tracking-wider rounded transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]",
                            isSubmitting && "opacity-50 cursor-not-allowed"
                        )}
                    >
                        {isSubmitting ? 'Saving...' : 'Save Changes'}
                    </button>
                </div>
            </div>
        </div>
    )
}
