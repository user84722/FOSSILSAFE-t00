/**
 * Demo Mode Provider
 * Mock data providers for testing without hardware dependencies
 */

import { createContext, useContext, useState, ReactNode } from 'react'
import type { Job, Tape } from './api'

interface DemoModeContextType {
    isDemoMode: boolean
    toggleDemoMode: () => void
    mockStats: any
    mockDrives: any[]
    mockTapes: Tape[]
    mockJobs: Job[]
    mockHwid: string
}

// Realistic mock data
export const MOCK_STATS = {
    cache_disk_percent: 67,
    cpu_percent: 23,
    ram_percent: 45,
    total_capacity_bytes: 1200 * 1024 * 1024 * 1024 * 1024,
    used_capacity_bytes: 450 * 1024 * 1024 * 1024 * 1024,
    tapes_online: 18,
    total_slots: 24,
    mailslot_enabled: true
};

export const MOCK_DRIVES = [
    { id: 'DRIVE_01', type: 'LTO-8', status: 'busy' as const, current_tape: 'TAPE002' },
    { id: 'DRIVE_02', type: 'LTO-8', status: 'idle' as const }
];

export const MOCK_TAPES: Tape[] = Array.from({ length: 24 }).map((_, i) => {
    const slotNum = i + 1;
    const barcode = `TAPE${String(slotNum).padStart(3, '0')}`;
    const isOffline = [7, 13, 21].includes(slotNum);
    const isInDrive = slotNum === 2;
    const isError = slotNum === 5;

    return {
        barcode,
        alias: slotNum % 3 === 0 ? `Archive_${barcode}` : undefined,
        status: isError ? 'error' : (isOffline ? 'offline' : (isInDrive ? 'in_use' : 'available')),
        location_type: isInDrive ? 'drive' : 'slot',
        slot_number: isInDrive ? undefined : slotNum,
        drive_id: isInDrive ? 'DRIVE_01' : undefined,
        capacity_bytes: 12_000_000_000_000,
        used_bytes: Math.floor(Math.random() * 8_000_000_000_000),
        ltfs_formatted: true,
        mount_count: Math.floor(Math.random() * 50),
        error_count: isError ? Math.floor(Math.random() * 5) + 1 : 0,
        trust_status: isError ? 'untrusted' : (slotNum % 4 === 0 ? 'partial' : 'trusted')
    };
});

export const MOCK_JOBS: Job[] = [
    { id: 1001, name: 'Daily Backup - Production', status: 'completed', type: 'backup', progress: 100, created_at: '2026-02-09T01:00:00Z', started_at: '2026-02-09T01:00:05Z', completed_at: '2026-02-09T03:45:22Z', bytes_written: 847_293_847_293, files_written: 12847 },
    {
        id: 1002,
        name: 'Weekly Verification',
        status: 'running',
        type: 'verify',
        progress: 67,
        created_at: '2026-02-09T04:00:00Z',
        started_at: '2026-02-09T04:00:12Z',
        bytes_written: 547_000_000_000,
        files_written: 8234,
        bytes_per_second: 312 * 1024 * 1024, // 312 MB/s
        files_per_second: 42.5,
        eta_seconds: 4520, // ~1h 15m
        feeder_rate_bps: 280 * 1024 * 1024,
        ingest_rate_bps: 315 * 1024 * 1024,
        buffer_health: 0.82
    },
    { id: 1003, name: 'Archive Migration', status: 'pending', type: 'duplicate', progress: 0, created_at: '2026-02-09T04:30:00Z', buffer_health: 0.1 },
    { id: 1004, name: 'Media Restore Request', status: 'completed', type: 'restore', progress: 100, created_at: '2026-02-08T14:22:00Z', started_at: '2026-02-08T14:25:00Z', completed_at: '2026-02-08T15:10:33Z', bytes_written: 42_500_000_000, files_written: 147 },
    { id: 1005, name: 'Failed Backup - Test', status: 'failed', type: 'backup', progress: 23, created_at: '2026-02-08T10:00:00Z', started_at: '2026-02-08T10:00:15Z' },
]


export const MOCK_HWID = 'FS-DEMO-HWID-1234567890ABCDEF'

const DemoModeContext = createContext<DemoModeContextType | null>(null)

export function DemoModeProvider({ children }: { children: ReactNode }) {
    const [isDemoMode, setIsDemoMode] = useState(() => {
        return localStorage.getItem('demoMode') === 'true'
    })


    const toggleDemoMode = () => {
        const newValue = !isDemoMode
        setIsDemoMode(newValue)
        localStorage.setItem('demoMode', String(newValue))
    }


    return (
        <DemoModeContext.Provider value={{
            isDemoMode,
            toggleDemoMode,
            mockStats: MOCK_STATS,
            mockDrives: MOCK_DRIVES,
            mockTapes: MOCK_TAPES,
            mockJobs: MOCK_JOBS,
            mockHwid: MOCK_HWID
        }}>
            {children}
        </DemoModeContext.Provider>
    )
}

export function useDemoMode() {
    const context = useContext(DemoModeContext)
    if (!context) {
        throw new Error('useDemoMode must be used within a DemoModeProvider')
    }
    return context
}
