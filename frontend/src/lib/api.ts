/**
 * FossilSafe API Client
 * Typed API methods with proper error handling and demo mode support
 */

// Types
export interface ApiResponse<T> {
    success: boolean
    data?: T
    error?: string
    code?: string
    detail?: string
}

export interface Job {
    id: number
    name: string
    status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'queued' | 'waiting_for_drive' | 'queued_waiting_media'
    type: 'backup' | 'restore' | 'duplicate' | 'verify' | 'archive' | 'tape_wipe' | 'tape_format' | 'tape_move' | 'tape_export' | 'tape_import' | 'diagnostics_run' | 'library_force_unload'
    progress: number
    created_at: string
    started_at?: string
    completed_at?: string
    source_id?: string
    source_path?: string
    target_tape?: string
    tapes?: string[]
    bytes_written?: number
    files_written?: number
    bytes_per_second?: number
    files_per_second?: number
    compression?: string
    encryption?: 'none' | 'software' | 'hardware'
    encryption_password?: string
    job_type?: string
    archival_policy?: string
    eta_seconds?: number
    feeder_rate_bps?: number
    ingest_rate_bps?: number
    buffer_health?: number // 0.0 to 1.0
    tape_barcode?: string
    preflight_passed?: boolean
    pre_job_hook?: string
    post_job_hook?: string
}

export interface Tape {
    barcode: string
    alias?: string
    status: 'available' | 'in_use' | 'full' | 'error' | 'offline'
    location_type: 'slot' | 'drive' | 'mailslot'
    slot_number?: number
    drive_id?: string
    capacity_bytes?: number
    used_bytes?: number
    volume_name?: string
    ltfs_formatted?: boolean
    mount_count?: number
    error_count?: number
    read_count?: number
    trust_status?: 'trusted' | 'partial' | 'untrusted' | 'unknown'
    worm_lock?: boolean
    retention_expires_at?: string
    is_cleaning_tape?: boolean
    cleaning_remaining_uses?: number
}


export interface User {
    id: number
    username: string
    role: 'admin' | 'operator' | 'viewer'
    is_active: boolean
    has_2fa: boolean
    created_at: string
    last_login?: string
}

export interface BackupSet {
    id: string
    sources: string[]
    created_at: string
}

export interface Snapshot {
    id: number
    backup_set_id: string
    job_id: number
    manifest_path: string
    total_files: number
    total_bytes: number
    tape_map: Record<string, number>
    created_at: string
}

export interface GraphNode {
    id: string
    type: 'set' | 'snapshot' | 'tape'
    label: string
    trust?: 'verified' | 'online' | 'unknown'
    data?: any
}

export interface GraphEdge {
    id: string
    source: string
    target: string
    label?: string
}

export interface GraphData {
    nodes: GraphNode[]
    edges: GraphEdge[]
}

// API Client class
export class ApiClient {
    private baseUrl = ''

    private getToken(): string | null {
        return localStorage.getItem('token')
    }

    private async request<T>(
        endpoint: string,
        options: RequestInit = {}
    ): Promise<ApiResponse<T>> {
        const token = this.getToken()

        // Fetch CSRF token for state-changing requests
        let csrfToken: string | null = null
        if (options.method && ['POST', 'PUT', 'DELETE', 'PATCH'].includes(options.method)) {
            try {
                const csrfRes = await fetch('/api/csrf-token', { credentials: 'include' })
                const csrfData = await csrfRes.json()
                csrfToken = csrfData.csrf_token
            } catch {
                // Continue without CSRF token
            }
        }

        const headers: HeadersInit = {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
            ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
            ...options.headers
        }

        try {
            const res = await fetch(`${this.baseUrl}${endpoint}`, {
                ...options,
                headers,
                credentials: 'include'
            })

            let data: any;
            const contentType = res.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
                try {
                    data = await res.json();
                } catch (e) {
                    data = { error: "FossilSafe backend is starting up or temporarily unavailable. This can take 15-30 seconds during boot. Please wait..." };
                }
            } else {
                const text = await res.text();
                if (res.status === 502 || res.status === 503 || res.status === 504 || text.includes("<html")) {
                    data = { error: "FossilSafe backend is starting up or temporarily unavailable. This can take 15-30 seconds during boot. Please wait..." };
                } else if (res.status === 404) {
                    data = { error: "API endpoint not found." };
                } else {
                    data = { error: `Unexpected response status: ${res.status}` };
                }
            }

            if (!res.ok) {
                // Determine if this is an auth failure
                if (res.status === 401) {
                    console.log('API: 401 Intercepted. Path:', window.location.pathname);
                    localStorage.removeItem('token');
                    // Do not auto-redirect, let the App/AuthContext handle state
                    console.log('API: Returning session expired error (No Redirect)');
                    return { success: false, error: 'Session expired' };
                }

                return {
                    success: false,
                    error: data.error?.message || data.error || 'Request failed',
                    code: data.code,
                    detail: data.detail
                }
            }

            // Normalize response to always have success and data property
            if (data && typeof data === 'object' && 'success' in data && typeof data.success === 'boolean') {
                const { success, error, code, detail, data: nestedData, ...rest } = data;
                return {
                    success,
                    error,
                    code,
                    detail,
                    data: nestedData || (Object.keys(rest).length > 0 ? rest : undefined)
                } as ApiResponse<T>;
            }
            return { success: true, data } as ApiResponse<T>;
        } catch (error) {
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Network error'
            }
        }
    }

    async get<T>(endpoint: string, options: RequestInit = {}) {
        return this.request<T>(endpoint, { ...options, method: 'GET' })
    }

    async post<T>(endpoint: string, body?: any, options: RequestInit = {}) {
        return this.request<T>(endpoint, {
            ...options,
            method: 'POST',
            body: body ? JSON.stringify(body) : undefined
        })
    }

    async put<T>(endpoint: string, body?: any, options: RequestInit = {}) {
        return this.request<T>(endpoint, {
            ...options,
            method: 'PUT',
            body: body ? JSON.stringify(body) : undefined
        })
    }

    async delete<T>(endpoint: string, options: RequestInit = {}) {
        return this.request<T>(endpoint, { ...options, method: 'DELETE' })
    }

    // Auth
    async getSetupStatus() {
        return this.request<{ setup_required: boolean; setup_mode?: 'secure' | 'relaxed' }>('/api/auth/setup-status')
    }

    async login(username: string, password: string, totpCode?: string) {
        const res = await this.request<{ token: string; username: string; role: string; id: number; has_2fa: boolean }>(
            '/api/auth/login',
            {
                method: 'POST',
                body: JSON.stringify({ username, password, totp_code: totpCode })
            }
        )
        if (res.success && res.data?.token) {
            localStorage.setItem('token', res.data.token)
        }
        return res
    }

    async setup(data: any) {
        return this.request<{ token: string; username: string; role: string; has_2fa: boolean }>('/api/auth/setup', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async setup2FA() {
        return this.request<{ secret: string; provisioning_uri: string }>('/api/auth/2fa/setup', {
            method: 'POST'
        })
    }

    async enable2FA(secret: string, code: string) {
        return this.request<{ message: string }>('/api/auth/2fa/enable', {
            method: 'POST',
            body: JSON.stringify({ secret, code })
        })
    }

    async getMe() {
        return this.request<User>('/api/auth/me')
    }

    async getUsers() {
        return this.request<{ users: User[] }>('/api/auth/users')
    }

    async createUser(userData: { username: string; password: string; role: 'admin' | 'operator' | 'viewer'; permissions?: string[] }) {
        return this.request<{ user_id: number }>('/api/auth/users', {
            method: 'POST',
            body: JSON.stringify(userData)
        })
    }

    async deleteUser(userId: number) {
        return this.request<void>(`/api/auth/users/${userId}`, { method: 'DELETE' })
    }

    async disable2FA(userId?: number) {
        const endpoint = userId ? `/api/auth/users/${userId}/disable-2fa` : '/api/auth/2fa/disable'
        return this.request<{ message: string }>(endpoint, { method: 'POST' })
    }

    async getNotificationSettings() {
        return this.request<{ settings: any }>(`/api/notifications/settings`)
    }

    async updateNotificationSettings(settings: any) {
        return this.request<void>(`/api/notifications/settings`, {
            method: 'POST',
            body: JSON.stringify(settings)
        })
    }

    async testNotification(provider: 'smtp' | 'webhook' | 'snmp') {
        return this.request<{ message: string }>(`/api/notifications/test`, {
            method: 'POST',
            body: JSON.stringify({ provider })
        })
    }

    // SSO/OIDC
    async getSSOConfig() {
        return this.request<{
            enabled: boolean;
            issuer: string;
            client_id: string;
            client_secret: string;
            redirect_uri?: string;
            provider_name?: string;
        }>('/api/auth/sso/config')
    }

    async updateSSOConfig(config: {
        enabled: boolean;
        issuer: string;
        client_id: string;
        client_secret: string;
    }) {
        return this.request<{ message: string; config: any }>('/api/auth/sso/config', {
            method: 'POST',
            body: JSON.stringify(config)
        })
    }


    // Jobs
    async getJobs(limit = 100, offset = 0) {
        return this.request<{ jobs: Job[]; total: number }>(`/api/jobs?limit=${limit}&offset=${offset}`)
    }

    async getSystemMounts() {
        return this.request<{ mounts: { id: string; name: string; path: string; type: string }[] }>('/api/system/mounts')
    }

    async getJob(jobId: number) {
        return this.request<{ job: Job; logs: string[] }>(`/api/jobs/${jobId}`)
    }

    async cancelJob(jobId: number) {
        return this.request<void>(`/api/jobs/${jobId}/cancel`, { method: 'POST' })
    }

    async createJob(jobData: Partial<Job>) {
        return this.request<{ job_id: number }>('/api/jobs', {
            method: 'POST',
            body: JSON.stringify(jobData)
        })
    }

    async runPreflight(jobData: Partial<Job>) {
        return this.request<any>('/api/jobs/preflight', {
            method: 'POST',
            body: JSON.stringify(jobData)
        })
    }

    async getHooks() {
        return this.request<{ pre: any[]; post: any[] }>('/api/jobs/hooks')
    }

    async runDryRun(jobData: Partial<Job>) {
        return this.request<{ result: any }>('/api/jobs/dryrun', {
            method: 'POST',
            body: JSON.stringify(jobData)
        })
    }

    // Schedules
    async getSchedules() {
        return this.request<{ schedules: any[] }>('/api/schedules')
    }

    async createSchedule(scheduleData: any) {
        return this.request<{ schedule_id: string }>('/api/schedules', {
            method: 'POST',
            body: JSON.stringify(scheduleData)
        })
    }

    async deleteSchedule(scheduleId: string) {
        return this.request<void>(`/api/schedules/${scheduleId}`, { method: 'DELETE' })
    }

    async updateSchedule(scheduleId: string, scheduleData: any) {
        return this.request<{ message: string }>(`/api/schedules/${scheduleId}`, {
            method: 'PUT',
            body: JSON.stringify(scheduleData)
        })
    }

    // Tapes
    async getTapes() {
        const res = await this.request<{ tapes: any[] }>('/api/tapes')
        if (res.success && res.data?.tapes) {
            res.data.tapes = res.data.tapes.map((t: any) => ({
                ...t,
                // Only map slot→slot_number for tapes physically in a slot.
                // Drive tapes keep slot_number undefined so they don't appear in the matrix.
                slot_number: t.location_type === 'drive'
                    ? undefined
                    : (t.slot_number ?? t.slot ?? undefined),
            }))
        }
        return res as ApiResponse<{ tapes: Tape[] }>
    }

    async importTape() {
        return this.request<{ message: string }>('/api/tapes/import', {
            method: 'POST'
        })
    }

    async exportTape(barcode: string) {
        return this.request<{ message: string }>(`/api/tapes/${barcode}/export`, {
            method: 'POST'
        })
    }

    async scanLibrary(mode: 'fast' | 'deep' = 'fast') {
        return this.request<{ tapes: Tape[] }>('/api/tapes/scan', {
            method: 'POST',
            body: JSON.stringify({ mode })
        })
    }

    async updateTapeAlias(barcode: string, alias: string | null) {
        return this.request<{ alias: string | null }>(`/api/tapes/${barcode}/alias`, {
            method: 'POST',
            body: JSON.stringify({ alias })
        })
    }

    async getTapeDetails(barcode: string) {
        return this.request<{ tape: Tape }>(`/api/tapes/${barcode}`)
    }

    async loadTape(barcode: string, drive = 0) {
        return this.request<{ message: string }>(`/api/library/load/${barcode}?drive=${drive}`, { method: 'POST' })
    }

    async unloadTape(drive = 0) {
        return this.request<{ message: string }>('/api/library/unload', {
            method: 'POST',
            body: JSON.stringify({ drive })
        })
    }

    async moveTape(source: any, destination: any) {
        return this.request<{ job_id: number }>('/api/tape/move', {
            method: 'POST',
            body: JSON.stringify({ source, destination })
        })
    }

    async wipeTape(barcode: string, drive = 0, erase_mode: 'quick' | 'format' | 'secure' = 'quick') {
        return this.request<{ job_id: number }>(`/api/tapes/${barcode}/wipe`, {
            method: 'POST',
            body: JSON.stringify({ confirmation: barcode, drive, erase_mode })
        })
    }

    async formatTape(barcode: string, drive = 0) {
        return this.request<{ job_id: number }>(`/api/tapes/${barcode}/format`, {
            method: 'POST',
            body: JSON.stringify({ drive })
        })
    }

    async lockTape(barcode: string, expiresAt: string) {
        return this.request<{ message: string }>(`/api/tapes/${barcode}/lock`, {
            method: 'POST',
            body: JSON.stringify({ expires_at: expiresAt })
        })
    }

    async getTapeManifest(barcode: string) {
        return this.request<{ manifest: any }>(`/api/tapes/${barcode}/manifest`)
    }

    async getTapeAlerts(barcode: string, limit = 50) {
        return this.request<{ alerts: any[] }>(`/api/tapes/${barcode}/alerts?limit=${limit}`)
    }

    async getCalibrationInfo() {
        return this.request<{
            potential_logical_paths: string[],
            physical_drive_count: number,
            inventory: any[],
            current_mapping: Record<string, string>
        }>('/api/library/calibration/info')
    }

    async identifyCalibrationDrive(mtxIndex: number, barcode: string) {
        return this.request<{ logical_path: string }>('/api/library/calibration/identify', {
            method: 'POST',
            body: JSON.stringify({ mtx_index: mtxIndex, barcode })
        })
    }

    async saveCalibrationMapping(mapping: Record<string, string>) {
        return this.request<{ success: boolean }>('/api/library/calibration/save', {
            method: 'POST',
            body: JSON.stringify({ mapping })
        })
    }

    async autoAliasTapes(overwrite = false) {
        return this.request<{ count: number }>('/api/tapes/auto-alias', {
            method: 'POST',
            body: JSON.stringify({ overwrite })
        })
    }

    async cleanDrive(driveId = 0) {
        return this.request<{ success: boolean; message: string }>('/api/drive/clean', {
            method: 'POST',
            body: JSON.stringify({ drive_id: driveId })
        })
    }

    // Restore
    async initiateRestore(data: {
        files: (string | { id?: number; file_path: string })[];
        destination: string;
        tape_barcode?: string;
        restoreType?: 'file' | 'system';
        confirm?: boolean;
        overwrite?: boolean;
        encryption_password?: string;
        simulation_mode?: boolean;
    }) {
        return this.request<{ restore_id: number; required_tapes: string[] }>('/api/restore/start', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async confirmTape(restoreId: number, barcode: string) {
        return this.request('/api/restore/' + restoreId + '/confirm-tape', {
            method: 'POST',
            body: JSON.stringify({ tape_barcode: barcode })
        })
    }

    async verifyTape(barcode: string, encryptionPassword?: string) {
        return this.request<any>('/api/restore/verify-tape', {
            method: 'POST',
            body: JSON.stringify({ barcode, encryption_password: encryptionPassword })
        })
    }

    async getRestoreJobs(limit = 50) {
        return this.request<{ jobs: any[] }>(`/api/restore/jobs?limit=${limit}`)
    }

    // Audit
    async getAuditLog(limit = 100, offset = 0) {
        return this.request<{ entries: Record<string, unknown>[]; limit: number; offset: number }>(
            `/api/audit?limit=${limit}&offset=${offset}`
        )
    }

    async verifyAuditChain() {
        return this.request<{ valid: boolean; total_entries: number; tampered_indices: number[] }>('/api/audit/verify')
    }

    async getComplianceStats() {
        return this.request<any>('/api/audit/compliance-stats')
    }

    async getComplianceReport() {
        return this.request<any>('/api/audit/compliance-report')
    }

    // Logs
    async getLogs(params: { level?: string; category?: string; limit?: number; offset?: number; since_id?: string }) {
        const query = new URLSearchParams(params as any).toString()
        return this.request<any>(`/api/logs?${query}`)
    }

    async logFrontendError(data: { level: string; message: string; category?: string; details?: any }) {
        return this.request<void>('/api/frontend/log', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async cleanupLogs(retentionDays = 30) {
        return this.request<{ deleted_count: number }>('/api/logs/cleanup', {
            method: 'POST',
            body: JSON.stringify({ retention_days: retentionDays })
        })
    }

    async getLogStats() {
        return this.request<any>('/api/logs/stats')
    }

    // Dashboard / System Status
    async getHealth() {
        return this.request<{ status: string; version: string; uptime?: number }>('/api/healthz')
    }

    async getDriveHealth() {
        return this.request<{
            drives: Array<{
                id: string;
                type: string;
                serial?: string;
                status: 'idle' | 'busy' | 'error' | 'offline' | 'reading' | 'writing';
                current_tape?: string;
                head_hours?: number;
                cleaning_needed?: boolean;
            }>
        }>('/api/drive/health')
    }

    async getPredictiveDriveHealth() {
        return this.request<{
            data: Record<string, {
                drive_id: string;
                score: number;
                status: 'healthy' | 'degraded' | 'critical' | 'unknown';
                alert_stats: { critical: number; warning: number; info: number; total: number };
            }>
        }>('/api/system/health/drives')
    }

    async getSystemStats() {
        return this.request<{
            cpu_percent: number;
            ram_percent: number;
            cache_disk_percent: number;
            total_capacity_bytes: number;
            used_capacity_bytes: number;
            tapes_online: number;
            total_slots: number;
            mailslot_enabled: boolean;
            mailslot_count: number;
        }>('/api/system/stats')
    }

    async getMailSlotConfig() {
        return this.request<{
            enabled: boolean;
            auto_detect: boolean;
            manual_index?: number;
        }>('/api/system/mailslots')
    }

    async updateMailSlotConfig(config: {
        enabled?: boolean;
        auto_detect?: boolean;
        manual_index?: number | null;
    }) {
        return this.request<{ message: string }>('/api/system/mailslots', {
            method: 'POST',
            body: JSON.stringify(config)
        })
    }

    // KMS
    async getKMSConfig() {
        return this.request<{
            type: 'local' | 'vault';
            vault_addr: string;
            mount_path: string;
            vault_token_configured: boolean;
        }>('/api/system/kms')
    }

    async updateKMSConfig(config: any) {
        return this.request<{ message: string }>('/api/system/kms', {
            method: 'POST',
            body: JSON.stringify(config)
        })
    }

    async rotateKmsKey() {
        return this.request<{ message: string }>('/api/system/kms/rotate', {
            method: 'POST'
        })
    }

    // Streaming
    async getStreamingConfig() {
        return this.request<{
            enabled: boolean;
            max_queue_size_gb: number;
            max_queue_files: number;
            producer_threads: number;
            staging_dir: string;
        }>('/api/system/streaming')
    }

    async updateStreamingConfig(config: any) {
        return this.request<{ message: string }>('/api/system/streaming', {
            method: 'POST',
            body: JSON.stringify(config)
        })
    }

    async getSystemInfo() {
        return this.request<{
            version: string;
            hostname: string;
            uptime: number;
            load_avg: [number, number, number];
            hardware_available: boolean;
            hardware_reason: string | null;
        }>('/api/system/info')
    }

    async getSSHPublicKey() {
        return this.request<{ success: boolean; public_key: string }>('/api/system/ssh-key')
    }

    // Catalog & Files
    async searchFiles(params: { query?: string; job_id?: number; tape_barcode?: string; extension?: string; limit?: number; offset?: number }) {
        return this.request<{ files: any[]; count: number }>('/api/files/search', {
            method: 'POST',
            body: JSON.stringify(params)
        })
    }

    async getFilesByTape(barcode: string) {
        return this.request<{ files: any[]; count: number }>(`/api/files/by-tape/${barcode}`)
    }

    async getFilesByJob(jobId: number) {
        return this.request<{ files: any[]; count: number }>(`/api/files/by-job/${jobId}`)
    }

    async exportCatalog(tapeBarcode: string) {
        return this.request<{ message: string }>('/api/catalog/export', {
            method: 'POST',
            body: JSON.stringify({ tape_barcode: tapeBarcode })
        })
    }

    async importCatalog(tapeBarcode: string) {
        return this.request<{ message: string; catalog_info: any }>('/api/catalog/import', {
            method: 'POST',
            body: JSON.stringify({ tape_barcode: tapeBarcode })
        })
    }
    async syncCatalog() {
        return this.request<{ message: string; results: string[] }>('/api/catalog/sync', {
            method: 'POST'
        })
    }

    // Sourcesaster Recovery
    async scanTapeForCatalog(tapeBarcode: string) {
        return this.request<{ catalog: any; trust_level: string; verification_message: string }>('/api/recovery/scan-tape', {
            method: 'POST',
            body: JSON.stringify({ tape_barcode: tapeBarcode })
        })
    }

    async startEmergencyDump(tapeBarcode: string, destination: string) {
        return this.request<{ job_id: number; message: string }>('/api/recovery/emergency-dump', {
            method: 'POST',
            body: JSON.stringify({ tape_barcode: tapeBarcode, destination_path: destination })
        })
    }

    async rebuildCatalog(params: { tape_barcodes?: string[]; scan_all?: boolean }) {
        return this.request<any>('/api/recovery/rebuild', {
            method: 'POST',
            body: JSON.stringify(params)
        })
    }

    async getRecoveryStatus() {
        return this.request<{ status: string; progress: number; current_stage: string }>('/api/recovery/status')
    }

    async getExternalCatalogBackups() {
        return this.request<{ backups: any[] }>('/api/catalog/backups')
    }

    // Sources
    async getSources() {
        return this.request<{ sources: any[] }>('/api/sources')
    }

    async createSource(data: any) {
        return this.request<{ message: string }>('/api/sources', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async deleteSource(sourceId: number) {
        return this.request<void>(`/api/sources/${sourceId}`, { method: 'DELETE' })
    }

    async testSourceConnection(data: any) {
        return this.request<{ message: string }>('/api/sources/test', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async estimatePathSize(path: string) {
        return this.request<{ total_size: number; file_count: number }>('/api/files/estimate-size', {
            method: 'POST',
            body: JSON.stringify({ path })
        })
    }

    async checkTapeCapacity(requiredBytes: number, tapes: string[]) {
        return this.request<{
            result: {
                sufficient: boolean;
                total_available: number;
                tapes: any[];
                shortage: number;
            }
        }>('/api/tapes/capacity-check', {
            method: 'POST',
            body: JSON.stringify({ required_bytes: requiredBytes, tapes: tapes })
        })
    }

    // Diagnostics
    async runDiagnostics() {
        return this.request<{ message: string; job_id: number }>('/api/diagnostics/run', { method: 'POST' })
    }

    async getDiagnosticsHealth() {
        return this.request<any>('/api/diagnostics/health')
    }

    async getDiagnosticsReports(limit = 20) {
        return this.request<{ reports: any[] }>(`/api/diagnostics/reports?limit=${limit}`)
    }

    async deleteDiagnosticsReport(reportId: number) {
        return this.request<{ message: string }>(`/api/diagnostics/reports/${reportId}`, { method: 'DELETE' })
    }

    async generateSupportBundle() {
        return this.request<{ message: string; bundle_path: string }>('/api/diagnostics/bundle', { method: 'POST' })
    }

    getSupportBundleUrl(path: string) {
        return `${this.baseUrl}/api/diagnostics/bundle/download?path=${encodeURIComponent(path)}`
    }

    async getDiagnosticsReportDownloadUrl(reportId: number, kind: 'json' | 'text' = 'json') {
        return `${this.baseUrl}/api/diagnostics/reports/${reportId}/download?kind=${kind}`
    }

    // Libraries
    async listLibraries() {
        return this.request<{ data: { id: string; display_name: string; is_default: boolean }[] }>('/api/system/libraries')
    }

    // Audit History
    async getAuditVerificationHistory(limit = 50) {
        return this.request<{ history: any[] }>(`/api/audit/verification-history?limit=${limit}`)
    }

    // KMS Status
    async getKMSStatus() {
        return this.request<any>('/api/kms/status')
    }

    // Webhooks
    async getWebhooks() {
        return this.request<{ webhooks: any[] }>('/api/webhooks')
    }

    async createWebhook(data: { url: string; name?: string; event_types?: string[]; secret?: string }) {
        return this.request<{ id: number }>('/api/webhooks', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async deleteWebhook(id: number) {
        return this.request<void>(`/api/webhooks/${id}`, { method: 'DELETE' })
    }

    async testWebhook(data: { url: string; secret?: string }) {
        return this.request<{ status: number; success: boolean; response: string }>('/api/webhooks/test', {
            method: 'POST',
            body: JSON.stringify(data)
        })
    }

    async getBackupSets() {
        return this.request<{ backup_sets: BackupSet[] }>('/api/backup-sets')
    }

    async getBackupSetDetails(id: string) {
        return this.request<{ backup_set: BackupSet; snapshots: Snapshot[] }>(`/api/backup-sets/${id}`)
    }

    async getBackupSetGraph(id: string) {
        return this.request<{ graph: GraphData }>(`/api/backup-sets/${id}/graph`)
    }
}

export const api = new ApiClient()
