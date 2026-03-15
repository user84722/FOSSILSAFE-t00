import { useState, useEffect } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"

interface LoginPageProps {
    onLogin: (token: string, username: string, role: string) => void
}

export default function LoginPage({ onLogin }: LoginPageProps) {
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [totpCode, setTotpCode] = useState('')
    const [error, setError] = useState('')
    const [isLoading, setIsLoading] = useState(false)
    const [requires2FA, setRequires2FA] = useState(false)
    const [ssoEnabled, setSsoEnabled] = useState(false)

    useEffect(() => {
        api.getSSOConfig().then(res => {
            if (res.success && res.data?.enabled) {
                setSsoEnabled(true)
            }
        })
    }, [])

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()

        if (!username || !password) {
            setError('Username and password are required')
            return
        }

        setIsLoading(true)
        setError('')

        try {
            console.log('Starting login process...');
            // Fetch CSRF token first
            console.log('Fetching CSRF token...');
            const csrfRes = await fetch('/api/csrf-token', { credentials: 'include' })
            console.log('CSRF Response status:', csrfRes.status);
            let csrfToken: string | null = null;
            try {
                const contentType = csrfRes.headers.get("content-type") || "";
                if (contentType.includes("application/json")) {
                    const csrfData = await csrfRes.json();
                    console.log('CSRF Data:', JSON.stringify(csrfData));
                    csrfToken = csrfData.data?.csrf_token || csrfData.csrf_token || null;
                }
            } catch (e) {
                console.warn('Could not parse CSRF token, backend might be starting up');
            }
            console.log('Extracted CSRF Token:', csrfToken);

            console.log('Sending login request...');
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(csrfToken && { 'X-CSRFToken': csrfToken })
                },
                credentials: 'include',
                body: JSON.stringify({
                    username,
                    password,
                    totp_code: totpCode || undefined
                })
            })
            console.log('Login Response status:', res.status);
            
            let data: any;
            const contentType = res.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
                try {
                    data = await res.json();
                } catch (e) {
                    data = { success: false, error: "FossilSafe backend is starting up or temporarily unavailable. This can take 15-30 seconds during boot. Please wait..." };
                }
            } else {
                const text = await res.text();
                if (res.status === 502 || res.status === 503 || res.status === 504 || text.includes("<html")) {
                    data = { success: false, error: "FossilSafe backend is starting up or temporarily unavailable. This can take 15-30 seconds during boot. Please wait..." };
                } else if (res.status === 404) {
                    data = { success: false, error: "API endpoint not found." };
                } else {
                    data = { success: false, error: `Unexpected response status: ${res.status}` };
                }
            }
            
            console.log('Login Response data:', JSON.stringify(data));

            if (data.require_2fa) {
                setRequires2FA(true)
                setError('')
                setIsLoading(false)
                return
            }

            if (!res.ok || !data.success) {
                throw new Error(data.error?.message || data.error || 'Login failed')
            }

            console.log('Login successful, setting token...');
            localStorage.setItem('token', data.data.token)
            onLogin(data.data.token, data.data.username, data.data.role)
        } catch (err: unknown) {
            console.error('Login error:', err);
            setError(err instanceof Error ? err.message : 'Login failed')
        } finally {
            console.log('Login process finished (finally block).');
            setIsLoading(false)
        }
    }

    return (
        <div className="min-h-screen bg-[#09090b] flex items-center justify-center p-8">
            <div className="w-full max-w-md">
                {/* Logo */}
                <div className="flex justify-center mb-8">
                    <div className="size-20 flex items-center justify-center rounded-lg bg-primary/10 border border-primary/20 shadow-[0_0_30px_rgba(25,230,100,0.15)] overflow-hidden">
                        <img src="/fossilsafe-logo.svg" alt="FossilSafe" className="size-20 object-contain" />
                    </div>
                </div>

                {/* Card */}
                <div className="bg-[#121214] border border-[#27272a] rounded-lg overflow-hidden shadow-2xl p-8">
                    <div className="text-center mb-6">
                        <h1 className="text-xl font-bold text-white uppercase tracking-tight">FossilSafe</h1>
                        <p className="text-xs text-[#71717a] mt-1">Open-Source LTO Backup System</p>
                    </div>

                    {error && (
                        <div className="mb-4 p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-xs">
                            {error}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Username</label>
                            <input
                                type="text"
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                placeholder="Enter username"
                                autoComplete="off"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Password</label>
                            <input
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                placeholder="Enter password"
                                autoComplete="off"
                            />
                        </div>

                        {requires2FA && (
                            <div className="space-y-2">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">2FA Code</label>
                                <input
                                    type="text"
                                    value={totpCode}
                                    onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all text-center tracking-[0.5em]"
                                    placeholder="000000"
                                    maxLength={6}
                                    autoFocus
                                />
                            </div>
                        )}

                        <button
                            type="submit"
                            disabled={isLoading}
                            className={cn(
                                "w-full py-3 bg-primary hover:bg-green-400 text-black font-bold rounded text-sm uppercase tracking-widest transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]",
                                isLoading && "opacity-50 cursor-not-allowed"
                            )}
                        >
                            {isLoading ? 'Authenticating...' : requires2FA ? 'Verify' : 'Login'}
                        </button>
                    </form>

                    {ssoEnabled && (
                        <div className="mt-6 pt-6 border-t border-[#27272a]">
                            <button
                                onClick={() => window.location.href = '/api/auth/sso/login'}
                                className="w-full py-3 bg-[#18181b] border border-[#27272a] text-[#71717a] hover:text-white font-bold rounded text-[10px] uppercase tracking-widest transition-all flex items-center justify-center gap-2"
                            >
                                <span className="material-symbols-outlined text-sm">login</span>
                                Login with SSO
                            </button>
                        </div>
                    )}
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
