import { useState } from "react"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

interface AddSourceModalProps {
    isOpen: boolean
    onClose: () => void
    onSuccess: () => void
}

type SourceType = 'smb' | 'nfs' | 's3' | 'ssh' | 'rsync' | 'local'

export default function AddSourceModal({ isOpen, onClose, onSuccess }: AddSourceModalProps) {
    const [name, setName] = useState('')
    const [type, setType] = useState<SourceType>('smb')
    const [config, setConfig] = useState<any>({})
    const [loading, setLoading] = useState(false)
    const [testing, setTesting] = useState(false)
    const [error, setError] = useState('')
    const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)
    const [sshPublicKey, setSshPublicKey] = useState<string>('')
    const [fetchingKey, setFetchingKey] = useState(false)

    const fetchSSHKey = async () => {
        setFetchingKey(true)
        try {
            const res = await api.getSSHPublicKey()
            if (res.success && res.data?.public_key) {
                setSshPublicKey(res.data.public_key)
            }
        } catch (err) {
            console.error("Failed to fetch SSH key", err)
        } finally {
            setFetchingKey(false)
        }
    }

    if (!isOpen) return null

    const handleConfigChange = (key: string, value: string) => {
        setConfig((prev: any) => ({ ...prev, [key]: value }))
    }

    const handleTest = async () => {
        setTesting(true)
        setTestResult(null)
        try {
            const res = await api.testSourceConnection({
                name,
                type,
                config
            })
            if (res.success) {
                setTestResult({ success: true, message: 'Connection successful' })
            } else {
                setTestResult({ success: false, message: res.error || 'Connection failed' })
            }
        } catch (err) {
            setTestResult({ success: false, message: 'Network error' })
        } finally {
            setTesting(false)
        }
    }

    const handleSubmit = async () => {
        if (!name) {
            setError('Name is required')
            return
        }

        setLoading(true)
        setError('')

        try {
            const res = await api.createSource({
                name,
                type,
                config
            })

            if (res.success) {
                onSuccess()
                handleClose()
            } else {
                setError(res.error || 'Failed to create source')
            }
        } catch (err) {
            setError('Network error')
        } finally {
            setLoading(false)
        }
    }

    const handleClose = () => {
        setName('')
        setType('smb')
        setConfig({})
        setError('')
        setTestResult(null)
        setSshPublicKey('')
        onClose()
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm">
            <div className="w-[500px] bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
                <div className="p-6 border-b border-[#27272a] flex justify-between items-center">
                    <h2 className="text-sm font-bold text-white uppercase tracking-[0.2em]">Add Data Source</h2>
                    <button onClick={handleClose} className="text-[#71717a] hover:text-white transition-colors">
                        <span className="material-symbols-outlined">close</span>
                    </button>
                </div>

                <div className="p-6 space-y-6">
                    {/* Name & Type */}
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] px-1">Source Name</label>
                            <input
                                type="text"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-2.5 text-xs text-white focus:border-primary/50 outline-none transition-all placeholder:text-[#3f3f46]"
                                placeholder="e.g., Production_NAS"
                            />
                        </div>

                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] px-1">Source Type</label>
                            <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
                                {(['smb', 'local', 'nfs', 'ssh', 'rsync', 's3'] as const).map(t => {
                                    return (
                                        <button
                                            key={t}
                                            onClick={() => {
                                                setType(t)
                                                if ((t === 'ssh' || t === 'rsync') && !sshPublicKey) {
                                                    fetchSSHKey()
                                                }
                                            }}
                                            className={cn(
                                                "flex flex-col items-center justify-center gap-2 py-3 rounded border transition-all relative overflow-hidden",
                                                type === t
                                                    ? "bg-primary/10 border-primary/50 text-white shadow-[0_0_10px_rgba(25,230,100,0.1)]"
                                                    : "bg-[#18181b] border-[#27272a] text-[#71717a] hover:bg-[#27272a]"
                                            )}
                                        >
                                            <span className="font-bold text-[10px] uppercase tracking-wider">{t}</span>
                                        </button>
                                    )
                                })}
                            </div>
                        </div>
                    </div>

                    {/* Configuration Fields */}
                    <div className="space-y-4 p-4 bg-[#18181b]/50 rounded border border-[#27272a]">
                        {type === 'smb' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Share Path (UNC)</label>
                                    <input
                                        type="text"
                                        value={config.path || ''}
                                        onChange={(e) => handleConfigChange('path', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="//server/share"
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Username</label>
                                        <input
                                            type="text"
                                            value={config.username || ''}
                                            onChange={(e) => handleConfigChange('username', e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs text-white focus:border-primary/50 outline-none"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Password</label>
                                        <input
                                            type="password"
                                            value={config.password || ''}
                                            onChange={(e) => handleConfigChange('password', e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs text-white focus:border-primary/50 outline-none"
                                        />
                                        <p className="text-[8px] text-[#71717a] mt-1 italic">
                                            Password is encrypted with AES-256 and never stored in plaintext.
                                        </p>
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Domain (Optional)</label>
                                    <input
                                        type="text"
                                        value={config.domain || ''}
                                        onChange={(e) => handleConfigChange('domain', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs text-white focus:border-primary/50 outline-none"
                                    />
                                </div>
                            </>
                        )}

                        {type === 's3' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Endpoint URL</label>
                                    <input
                                        type="text"
                                        value={config.endpoint || ''}
                                        onChange={(e) => handleConfigChange('endpoint', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="https://s3.amazonaws.com"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Bucket</label>
                                    <input
                                        type="text"
                                        value={config.bucket || ''}
                                        onChange={(e) => handleConfigChange('bucket', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs text-white focus:border-primary/50 outline-none"
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Access Key</label>
                                        <input
                                            type="text"
                                            value={config.access_key || ''}
                                            onChange={(e) => handleConfigChange('access_key', e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Secret Key</label>
                                        <input
                                            type="password"
                                            value={config.secret_key || ''}
                                            onChange={(e) => handleConfigChange('secret_key', e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        />
                                        <p className="text-[8px] text-[#71717a] mt-1 italic">
                                            Keys are encrypted with AES-256 and never stored in plaintext.
                                        </p>
                                    </div>
                                </div>
                            </>
                        )}

                        {type === 'nfs' && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">NFS Server (IP or Hostname)</label>
                                    <input
                                        type="text"
                                        value={config.nfs_server || ''}
                                        onChange={(e) => handleConfigChange('nfs_server', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="192.168.1.10"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Export Path</label>
                                    <input
                                        type="text"
                                        value={config.nfs_export || ''}
                                        onChange={(e) => handleConfigChange('nfs_export', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="/srv/nfs/share"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Mount Options (Optional)</label>
                                    <input
                                        type="text"
                                        value={config.nfs_options || ''}
                                        onChange={(e) => handleConfigChange('nfs_options', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="nolock,ro"
                                    />
                                </div>
                            </>
                        )}

                        {(type === 'ssh' || type === 'rsync') && (
                            <>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Remote Host</label>
                                    <input
                                        type="text"
                                        value={config.host || ''}
                                        onChange={(e) => handleConfigChange('host', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="192.168.1.50"
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Username</label>
                                        <input
                                            type="text"
                                            value={config.username || ''}
                                            onChange={(e) => handleConfigChange('username', e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs text-white focus:border-primary/50 outline-none"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Port</label>
                                        <input
                                            type="number"
                                            value={config.port || '22'}
                                            onChange={(e) => handleConfigChange('port', e.target.value)}
                                            className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs text-white focus:border-primary/50 outline-none"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Remote Path</label>
                                    <input
                                        type="text"
                                        value={config.path || ''}
                                        onChange={(e) => handleConfigChange('path', e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                        placeholder="/var/data/backups"
                                    />
                                </div>

                                {sshPublicKey || fetchingKey ? (
                                    <div className="space-y-2 pt-2 border-t border-[#27272a]">
                                        <label className="text-[9px] font-bold text-primary uppercase tracking-[0.2em]">Appliance SSH Public Key</label>
                                        <div className="relative group">
                                            {fetchingKey ? (
                                                <div className="w-full h-16 bg-black/60 border border-primary/20 rounded flex items-center justify-center">
                                                    <span className="text-[10px] text-primary animate-pulse uppercase font-bold tracking-widest">Fetching Key...</span>
                                                </div>
                                            ) : (
                                                <>
                                                    <textarea
                                                        readOnly
                                                        value={sshPublicKey}
                                                        className="w-full h-16 bg-black/60 border border-primary/20 rounded px-2 py-1.5 text-[8px] font-mono text-[#a1a1aa] focus:outline-none resize-none"
                                                    />
                                                    <button
                                                        onClick={() => navigator.clipboard.writeText(sshPublicKey)}
                                                        className="absolute top-1 right-1 p-1 bg-[#27272a] hover:bg-[#3f3f46] rounded text-[8px] text-white opacity-0 group-hover:opacity-100 transition-opacity"
                                                    >
                                                        Copy
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                        <p className="text-[8px] text-[#71717a] leading-relaxed italic">
                                            Add this key to <code className="text-white">~/.ssh/authorized_keys</code> on the remote host.
                                        </p>
                                    </div>
                                ) : null}
                            </>
                        )}

                        {type === 'local' && (
                            <div className="space-y-2">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em]">Absolute Local Path</label>
                                <input
                                    type="text"
                                    value={config.path || ''}
                                    onChange={(e) => handleConfigChange('path', e.target.value)}
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-xs font-mono text-white focus:border-primary/50 outline-none"
                                    placeholder="/mnt/storage/data"
                                />
                                <p className="text-[8px] text-[#71717a] leading-relaxed italic">
                                    Path must be accessible by the FossilSafe service account.
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Status Messages */}
                    {error && (
                        <div className="p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-[10px] font-bold flex items-center gap-2">
                            <span className="material-symbols-outlined text-sm">error</span>
                            {error}
                        </div>
                    )}

                    {testResult && (
                        <div className={cn(
                            "p-3 rounded border text-[10px] font-bold flex items-center gap-2",
                            testResult.success ? "bg-primary/10 border-primary/20 text-primary" : "bg-destructive/10 border-destructive/20 text-destructive"
                        )}>
                            <span className="material-symbols-outlined text-sm">
                                {testResult.success ? 'check_circle' : 'error'}
                            </span>
                            {testResult.message}
                        </div>
                    )}
                </div>

                <div className="p-6 border-t border-[#27272a] bg-[#18181b]/30 flex justify-between items-center">
                    <button
                        onClick={handleTest}
                        disabled={testing || loading}
                        className="px-4 py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white rounded text-[10px] font-bold uppercase tracking-[0.2em] transition-all flex items-center gap-2 disabled:opacity-50"
                    >
                        {testing ? (
                            <span className="material-symbols-outlined text-sm animate-spin">sync</span>
                        ) : (
                            <span className="material-symbols-outlined text-sm">wifi</span>
                        )}
                        Test Connection
                    </button>

                    <div className="flex gap-3">
                        <button
                            onClick={handleClose}
                            className="px-4 py-2 text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-[0.2em] transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleSubmit}
                            disabled={loading || testing}
                            className="px-6 py-2 bg-primary hover:bg-green-400 text-black rounded text-[10px] font-bold uppercase tracking-[0.2em] shadow-[0_4px_12px_rgba(25,230,100,0.2)] transition-all flex items-center gap-2 disabled:opacity-50"
                        >
                            {loading ? 'Saving...' : 'Add Source'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}
