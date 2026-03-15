import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from './api'

// Mock global fetch
global.fetch = vi.fn()

describe('ApiClient', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        // Mock localStorage
        const localStorageMock = (() => {
            let store: Record<string, string> = {}
            return {
                getItem: vi.fn((key: string) => store[key] || null),
                setItem: vi.fn((key: string, value: string) => {
                    store[key] = value.toString()
                }),
                clear: vi.fn(() => {
                    store = {}
                }),
                removeItem: vi.fn((key: string) => {
                    delete store[key]
                })
            }
        })()
        Object.defineProperty(global, 'localStorage', { value: localStorageMock })
    })

    describe('cleanupLogs', () => {
        it('should call cleanup endpoint with retention days', async () => {
            const mockResponse = { success: true, deleted_count: 50 }
                ; (fetch as any).mockResolvedValue({
                    ok: true,
                    json: () => Promise.resolve(mockResponse)
                })

            const result = await api.cleanupLogs(30)
            expect(result.success).toBe(true)
            expect(result.data?.deleted_count).toBe(50)

            // Should fetch CSRF token first because it's a POST
            expect(fetch).toHaveBeenCalledWith('/api/csrf-token', expect.any(Object))
            expect(fetch).toHaveBeenCalledWith('/api/logs/cleanup', expect.objectContaining({
                method: 'POST',
                body: JSON.stringify({ retention_days: 30 })
            }))
        })
    })

    describe('unloadTape', () => {
        it('should call unload endpoint', async () => {
            const mockResponse = { success: true, message: 'Unload started' }
                ; (fetch as any).mockResolvedValue({
                    ok: true,
                    json: () => Promise.resolve(mockResponse)
                })

            const result = await api.unloadTape(0)
            expect(result.success).toBe(true)
            expect(fetch).toHaveBeenCalledWith('/api/library/unload', expect.objectContaining({
                method: 'POST',
                body: JSON.stringify({ drive: 0 })
            }))
        })
    })

    describe('wipeTape', () => {
        it('should call wipe endpoint for specified barcode', async () => {
            const mockResponse = { success: true, job_id: 123 }
                ; (fetch as any).mockResolvedValue({
                    ok: true,
                    json: () => Promise.resolve(mockResponse)
                })

            const result = await api.wipeTape('TAPE01', 0)
            expect(result.success).toBe(true)
            expect(result.data?.job_id).toBe(123)
            expect(fetch).toHaveBeenCalledWith('/api/tapes/TAPE01/wipe', expect.objectContaining({
                method: 'POST',
                body: JSON.stringify({ confirmation: 'TAPE01', drive: 0 })
            }))
        })
    })
})
