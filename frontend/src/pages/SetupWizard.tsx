import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"
import { QRCodeSVG } from "qrcode.react"

type SetupStep = 'welcome' | 'secure-check' | 'admin' | '2fa' | 'tape-init' | 'complete'

interface SetupWizardProps {
    onComplete: (token: string, username: string, role: string) => void
}

export default function SetupWizard({ onComplete }: SetupWizardProps) {
    const [step, setStep] = useState<SetupStep>('welcome')
    const [setupMode, setSetupMode] = useState<'relaxed' | 'secure'>('relaxed')
    const [apiKey, setApiKey] = useState('')
    const [isRecoveryMode, setIsRecoveryMode] = useState(false)

    // Admin
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [confirmPassword, setConfirmPassword] = useState('')

    // 2FA
    const [totpSecret, setTotpSecret] = useState('')
    const [totpUri, setTotpUri] = useState('')
    const [totpCode, setTotpCode] = useState('')


    // Tape Init
    const [tapeStatus, setTapeStatus] = useState<{
        has_library: boolean;
        count: number;
        already_initialized: boolean;
        initialized_count: number;
    } | null>(null)
    const [initProgress, setInitProgress] = useState<{
        running: boolean;
        current: number;
        total: number;
        last_barcode: string | null;
        error: string | null;
        complete: boolean;
    } | null>(null)

    const [error, setError] = useState('')
    const [isLoading, setIsLoading] = useState(false)

    useEffect(() => {
        checkStatus()
    }, [])

    const checkStatus = async () => {
        try {
            const res = await api.getSetupStatus()
            if (res.success && res.data?.setup_mode) {
                setSetupMode(res.data.setup_mode)
            }
        } catch (e) {
            console.error("Failed to check setup status", e)
        }
    }

    const handleBegin = () => {
        if (setupMode === 'secure') {
            setStep('secure-check')
        } else {
            setStep('admin')
        }
    }

    const handleSecureSubmit = () => {
        if (!apiKey.trim()) {
            setError('API Key is required in Secure Mode')
            return
        }
        localStorage.setItem('apiKey', apiKey.trim()) // Persist for session
        setError('')
        setStep('admin')
    }

    const handleAdminSubmit = async () => {
        if (!username || !password) {
            setError('Username and password are required')
            return
        }
        if (password !== confirmPassword) {
            setError('Passwords do not match')
            return
        }
        if (password.length < 8) {
            setError('Password must be at least 8 characters')
            return
        }

        setIsLoading(true)
        setError('')

        try {
            // Include API Key if in secure mode
            const payload: any = { username, password }
            if (setupMode === 'secure') {
                payload.api_key = apiKey
            }

            const res = await api.setup(payload)

            if (!res.success) {
                throw new Error(res.error || 'Setup failed')
            }

            // Store token immediately to allow 2FA setup calls
            if (res.data?.token) {
                localStorage.setItem('token', res.data.token)
            }

            // Streamlined DR Flow: Skip 2FA
            if (isRecoveryMode) {
                setStep('complete')
                return
            }

            // Standard Flow: Move to 2FA
            await prepare2FA()
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Setup failed')
        } finally {
            setIsLoading(false)
        }
    }

    const prepare2FA = async () => {
        setIsLoading(true)
        try {
            const res = await api.setup2FA()
            if (res.success && res.data) {
                setTotpSecret(res.data.secret)
                setTotpUri(res.data.provisioning_uri)
                setStep('2fa')
            } else {
                throw new Error(res.error || "Failed to generate 2FA")
            }
        } catch (e: any) {
            setError(e.message)
        } finally {
            setIsLoading(false)
        }
    }

    const verify2FA = async () => {
        if (!totpCode || totpCode.length !== 6) {
            setError('Enter a valid 6-digit code')
            return
        }
        setIsLoading(true)
        try {
            const res = await api.enable2FA(totpSecret, totpCode)
            if (res.success) {
                await checkTapeStep()
            } else {
                setError(res.error || "Invalid code")
            }
        } catch (e: any) {
            setError(e.message || "Failed to verify 2FA")
        } finally {
            setIsLoading(false)
        }
    }


    const checkTapeStep = async () => {
        setIsLoading(true)
        try {
            const res = await (api as any).request('GET', '/api/setup/tape-status')
            if (res.success && res.data?.has_library) {
                setTapeStatus(res.data)
                setStep('tape-init')
            } else {
                setStep('complete')
            }
        } catch (e) {
            console.error("Tape status check failed", e)
            setStep('complete')
        } finally {
            setIsLoading(false)
        }
    }

    const handleStartTapeInit = async () => {
        setError('')
        try {
            const res = await (api as any).request('POST', '/api/setup/tape-init')
            if (res.success) {
                pollTapeInit()
            } else {
                setError(res.error || "Failed to start tape initialization")
            }
        } catch (e: any) {
            setError(e.message || "Failed to start tape initialization")
        }
    }

    const pollTapeInit = async () => {
        const interval = setInterval(async () => {
            try {
                const res = await (api as any).request('GET', '/api/setup/tape-init/status')
                if (res.success && res.data) {
                    setInitProgress(res.data)
                    if (res.data.complete) {
                        clearInterval(interval)
                        setTimeout(() => setStep('complete'), 2000)
                    } else if (res.data.error) {
                        clearInterval(interval)
                        setError(res.data.error)
                    }
                }
            } catch (e) {
                console.error("Polling tape init failed", e)
            }
        }, 2000)
    }

    const handleComplete = () => {
        const token = localStorage.getItem('token') || ''

        if (isRecoveryMode) {
            window.location.href = "/recovery" // Hard override for disaster recovery
        } else {
            onComplete(token, username, 'admin')
        }
    }

    return (
        <div className="min-h-screen bg-[#09090b] flex items-center justify-center p-8">
            <div className="w-full max-w-lg">
                {/* Logo */}
                <div className="flex justify-center mb-8">
                    <div className="size-20 flex items-center justify-center rounded-lg bg-primary/10 border border-primary/20 shadow-[0_0_30px_rgba(25,230,100,0.15)] overflow-hidden">
                        <img src="/fossilsafe-logo.svg" alt="FossilSafe" className="size-20 object-contain" />
                    </div>
                </div>

                {/* Card */}
                <div className="bg-[#121214] border border-[#27272a] rounded-lg overflow-hidden shadow-2xl">
                    {/* Progress Bar */}
                    <div className="h-1 bg-[#27272a] flex">
                        {['welcome', 'secure-check', 'admin', '2fa', 'tape-init', 'complete'].map((s) => {
                            // Simple progress logic
                            const currentIdx = ['welcome', 'secure-check', 'admin', '2fa', 'tape-init', 'complete'].indexOf(step)
                            const stepIdx = ['welcome', 'secure-check', 'admin', '2fa', 'tape-init', 'complete'].indexOf(s)
                            const isActive = stepIdx <= currentIdx

                            if (setupMode !== 'secure' && s === 'secure-check') return null

                            // Hide 2fa in recovery mode
                            if (isRecoveryMode && s === '2fa') return null

                            // Skip tape-init in progress display if no library as well during visual map
                            if (s === 'tape-init' && step !== 'tape-init' && !tapeStatus?.has_library) return null

                            return (
                                <div key={s} className={cn("h-full transition-all duration-500", isActive ? "bg-primary" : "bg-transparent")} style={{ flex: 1 }} />
                            )
                        })}
                    </div>

                    {/* Content */}
                    <div className="p-8">
                        {step === 'welcome' && (
                            <div className="text-center space-y-6">
                                <div>
                                    <h1 className="text-2xl font-bold text-white uppercase tracking-tight">Welcome to FossilSafe</h1>
                                    <p className="text-sm text-[#71717a] mt-2">Open-Source LTO Tape Backup System</p>
                                </div>
                                <div className="py-6 space-y-4">
                                    <div className="flex items-center gap-4 text-left p-4 rounded bg-black/20 border border-[#27272a]">
                                        <span className="material-symbols-outlined text-primary text-2xl">security</span>
                                        <div>
                                            <div className="text-xs font-bold text-white uppercase tracking-wider">Secure Setup</div>
                                            <div className="text-[10px] text-[#71717a]">
                                                {setupMode === 'secure' ? 'Strict Mode: API Key Required' : 'Relaxed Mode: Standard Setup'}
                                            </div>
                                        </div>
                                    </div>

                                    <div onClick={() => setIsRecoveryMode(!isRecoveryMode)} className={cn("flex items-center gap-4 text-left p-4 rounded border cursor-pointer transition-all", isRecoveryMode ? "bg-red-500/10 border-red-500/50" : "bg-black/20 border-[#27272a] hover:border-[#3f3f46]")}>
                                        <span className={cn("material-symbols-outlined text-2xl", isRecoveryMode ? "text-red-500" : "text-[#71717a]")}>warning</span>
                                        <div>
                                            <div className={cn("text-xs font-bold uppercase tracking-wider", isRecoveryMode ? "text-red-400" : "text-[#71717a]")}>Disaster Recovery</div>
                                            <div className="text-[10px] text-[#71717a]">I am restoring a lost system from tape</div>
                                        </div>
                                        <div className={cn("ml-auto size-4 rounded-full border flex items-center justify-center", isRecoveryMode ? "border-red-500 bg-red-500" : "border-[#71717a]")}>
                                            {isRecoveryMode && <span className="material-symbols-outlined text-[10px] text-white">check</span>}
                                        </div>
                                    </div>
                                </div>
                                <button
                                    onClick={handleBegin}
                                    className="w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]"
                                >
                                    Begin Setup
                                </button>
                            </div>
                        )}

                        {step === 'secure-check' && (
                            <div className="space-y-6">
                                <div className="text-center">
                                    <h2 className="text-lg font-bold text-white uppercase tracking-tight">Security Check</h2>
                                    <p className="text-xs text-[#71717a] mt-1">Enter the installation API Key to proceed</p>
                                </div>
                                {error && <div className="p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs">{error}</div>}
                                <input
                                    type="password"
                                    value={apiKey}
                                    onChange={(e) => setApiKey(e.target.value)}
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                    placeholder="Installation API Key"
                                />
                                <button onClick={handleSecureSubmit} className="w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all">
                                    Verify & Continue
                                </button>
                            </div>
                        )}

                        {step === 'admin' && (
                            <div className="space-y-6">
                                <div className="text-center">
                                    <h2 className="text-lg font-bold text-white uppercase tracking-tight">Create Admin Account</h2>
                                    <p className="text-xs text-[#71717a] mt-1">This will be the primary administrator</p>
                                    {isRecoveryMode && (
                                        <div className="mt-4 p-3 bg-blue-500/10 border border-blue-500/20 rounded text-left">
                                            <div className="flex items-center gap-2 mb-1">
                                                <span className="material-symbols-outlined text-blue-400 text-sm">info</span>
                                                <span className="text-[10px] font-bold text-blue-400 uppercase tracking-wider">Recovery Mode</span>
                                            </div>
                                            <p className="text-[10px] text-[#a1a1aa] leading-relaxed">
                                                Create a temporary admin account to access the restore interface.
                                                If you restore a full system backup, this account may be overwritten by the restored database.
                                            </p>
                                        </div>
                                    )}
                                </div>

                                {error && (
                                    <div className="p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs">
                                        {error}
                                    </div>
                                )}

                                <div className="space-y-4">
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Username</label>
                                        <input
                                            type="text"
                                            value={username}
                                            onChange={(e) => setUsername(e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                            placeholder="admin"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Password</label>
                                        <input
                                            type="password"
                                            value={password}
                                            onChange={(e) => setPassword(e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                            placeholder="••••••••"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Confirm Password</label>
                                        <input
                                            type="password"
                                            value={confirmPassword}
                                            onChange={(e) => setConfirmPassword(e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                            placeholder="••••••••"
                                        />
                                    </div>
                                </div>

                                <button
                                    onClick={handleAdminSubmit}
                                    disabled={isLoading}
                                    className={cn(
                                        "w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]",
                                        isLoading && "opacity-50 cursor-not-allowed"
                                    )}
                                >
                                    {isLoading ? 'Creating...' : 'Create Account'}
                                </button>
                            </div>
                        )}

                        {step === '2fa' && (
                            <div className="space-y-6 text-center">
                                <div>
                                    <h2 className="text-lg font-bold text-white uppercase tracking-tight">Mandatory 2FA Setup</h2>
                                    <p className="text-xs text-[#71717a] mt-1">Scan the QR code with your authenticator app</p>
                                </div>

                                {error && <div className="text-destructive text-xs">{error}</div>}

                                <div className="flex justify-center p-4 bg-white rounded-lg mx-auto w-fit">
                                    {totpUri ? <QRCodeSVG value={totpUri} size={150} /> : <div className="size-[150px] animate-pulse bg-gray-200" />}
                                </div>

                                <div className="text-xs font-mono text-[#71717a] break-all px-4">
                                    {totpSecret}
                                </div>

                                <div className="space-y-2">
                                    <input
                                        type="text"
                                        value={totpCode}
                                        onChange={(e) => setTotpCode(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-center text-lg font-mono text-white focus:border-primary/50 outline-none tracking-widest"
                                        placeholder="000 000"
                                        maxLength={6}
                                    />
                                </div>

                                <button
                                    onClick={verify2FA}
                                    disabled={isLoading || totpCode.length !== 6}
                                    className={cn(
                                        "w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all",
                                        (isLoading || totpCode.length !== 6) && "opacity-50 cursor-not-allowed"
                                    )}
                                >
                                    {isLoading ? 'Verifying...' : 'Verify & Enable 2FA'}
                                </button>
                            </div>
                        )}


                        {step === 'tape-init' && (
                            <div className="space-y-6 text-center">
                                <div>
                                    <h2 className="text-xl font-bold text-white uppercase tracking-tight">Initialize Tape Library</h2>
                                    <p className="text-xs text-[#71717a] mt-1">Prepare your LTO tapes for FossilSafe</p>
                                </div>

                                <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-left space-y-3">
                                    <div className="flex items-center gap-2 text-yellow-500">
                                        <span className="material-symbols-outlined text-lg">warning</span>
                                        <span className="text-[10px] font-bold uppercase tracking-wider">Warning: Data Wipe</span>
                                    </div>
                                    <p className="text-xs text-[#a1a1aa] leading-relaxed">
                                        This process will format all <span className="text-white font-bold">{tapeStatus?.count || 0}</span> discovered tapes using LTFS. 
                                        <strong className="text-white"> Any existing data on these tapes will be permanently lost.</strong>
                                    </p>
                                    {tapeStatus?.already_initialized && (
                                        <div className="pt-2 border-t border-yellow-500/20 text-[10px] text-yellow-400 font-mono">
                                            {tapeStatus.initialized_count} tapes appear to be already formatted.
                                        </div>
                                    )}
                                </div>

                                {initProgress?.running || initProgress?.complete ? (
                                    <div className="space-y-4 py-4">
                                        <div className="flex items-center justify-between text-[10px] uppercase font-bold tracking-widest">
                                            <span className="text-[#a1a1aa]">
                                                {initProgress.complete ? 'Initialization Complete' : `Processing Tape ${initProgress.current} of ${initProgress.total}`}
                                            </span>
                                            <span className="text-primary">{Math.round((initProgress.current / initProgress.total) * 100)}%</span>
                                        </div>
                                        <div className="h-2 bg-black/40 rounded-full overflow-hidden border border-[#27272a]">
                                            <div 
                                                className="h-full bg-primary transition-all duration-500 shadow-[0_0_10px_rgba(25,230,100,0.5)]" 
                                                style={{ width: `${(initProgress.current / initProgress.total) * 100}%` }}
                                            />
                                        </div>
                                        <div className="text-[10px] font-mono text-[#71717a] flex items-center justify-center gap-2">
                                            {initProgress.complete ? (
                                                <span className="text-primary flex items-center gap-1">
                                                    <span className="material-symbols-outlined text-sm">check_circle</span>
                                                    All tapes ready
                                                </span>
                                            ) : (
                                                <>
                                                    <span className="size-1.5 rounded-full bg-primary animate-pulse" />
                                                    Formatting: {initProgress.last_barcode || 'Reading...'}
                                                </>
                                            )}
                                        </div>
                                    </div>
                                ) : (
                                    <div className="space-y-4">
                                        <button
                                            onClick={handleStartTapeInit}
                                            disabled={isLoading}
                                            className="w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]"
                                        >
                                            Initialize All Tapes
                                        </button>
                                        <button
                                            onClick={() => setStep('complete')}
                                            disabled={isLoading}
                                            className="w-full py-2 text-xs text-[#71717a] hover:text-white uppercase tracking-widest font-bold transition-colors"
                                        >
                                            Skip & Proceed to Main UI
                                        </button>
                                    </div>
                                )}

                                {error && <div className="p-3 rounded bg-red-500/10 border border-red-500/20 text-red-500 text-xs">{error}</div>}
                            </div>
                        )}

                        {step === 'complete' && (
                            <div className="text-center space-y-6">
                                <div className="size-16 mx-auto rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center">
                                    <span className="material-symbols-outlined text-primary text-3xl">check_circle</span>
                                </div>
                                <div>
                                    <h2 className="text-lg font-bold text-white uppercase tracking-tight">Setup Complete</h2>
                                    <p className="text-xs text-[#71717a] mt-1">
                                        {isRecoveryMode ? 'Redirecting to Disaster Recovery...' : 'Your FossilSafe system is ready'}
                                    </p>
                                </div>
                                <button
                                    onClick={() => {
                                        localStorage.removeItem('fossil_safe_onboarding_seen');
                                        handleComplete();
                                    }}
                                    className="w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]"
                                >
                                    {isRecoveryMode ? 'Enter Recovery Mode' : 'Start Tour & Enter Dashboard'}
                                </button>
                                <button
                                    onClick={() => {
                                        localStorage.setItem('fossil_safe_onboarding_seen', 'true');
                                        handleComplete();
                                    }}
                                    className="block w-full text-xs text-[#71717a] hover:text-white underline"
                                >
                                    Skip Tour
                                </button>
                            </div>
                        )}
                    </div>
                </div>

                {/* Footer */}
                <div className="text-center mt-6 text-[10px] font-mono uppercase tracking-widest">
                    <a href="https://fossilsafe.io" target="_blank" rel="noopener noreferrer" className="text-[#71717a] hover:text-primary transition-colors">
                        fossilsafe.io
                    </a>
                </div>
            </div>
        </div>
    )
}
