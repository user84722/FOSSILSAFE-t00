import { useState } from "react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"

interface AddUserModalProps {
    isOpen: boolean
    onClose: () => void
    onSuccess?: () => void
}

type UserRole = 'Administrator' | 'Operator' | 'Read-Only'

interface Permission {
    id: string
    label: string
    description: string
    checked: boolean
}

export default function AddUserModal({ isOpen, onClose, onSuccess }: AddUserModalProps) {
    const [username, setUsername] = useState('')
    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [confirmPassword, setConfirmPassword] = useState('')
    const [role, setRole] = useState<UserRole>('Operator')
    const [showPassword, setShowPassword] = useState(false)
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState('')

    const [permissions, setPermissions] = useState<Permission[]>([
        { id: 'jobs', label: 'Create & Manage Jobs', description: 'Can start, stop and delete backup/restore jobs', checked: true },
        { id: 'tapes', label: 'Manage Media', description: 'Can format, erase and checkout tapes', checked: true },
        { id: 'settings', label: 'System Settings', description: 'Can modify configuration and network settings', checked: false },
        { id: 'users', label: 'User Management', description: 'Can add/remove users and change roles', checked: false },
    ])

    const handleTogglePermission = (id: string) => {
        setPermissions(prev => prev.map(p => p.id === id ? { ...p, checked: !p.checked } : p))
    }

    const handleCreateUser = async () => {
        if (!username || !password) {
            setError('Username and password are required')
            return
        }
        if (password !== confirmPassword) {
            setError('Passwords do not match')
            return
        }

        setIsLoading(true)
        setError('')

        try {
            // Map UI roles to Backend roles
            const roleMap: Record<string, 'admin' | 'operator' | 'viewer'> = {
                'Administrator': 'admin',
                'Operator': 'operator',
                'Read-Only': 'viewer'
            }
            const backendRole = roleMap[role] || 'viewer'

            const res = await api.createUser({
                username,
                password,
                role: backendRole
            })

            if (res.success) {
                onSuccess?.()
                onClose()
            } else {
                setError(res.error || 'Failed to create user')
            }
        } catch (err) {
            setError('Failed to create user. Network error.')
        } finally {
            setIsLoading(false)
        }
    }

    if (!isOpen) return null

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/80 backdrop-blur-sm"
                onClick={onClose}
            />

            {/* Modal */}
            <div className="relative w-full max-w-2xl bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden flex flex-col md:flex-row">
                {/* Left Side: General Info */}
                <div className="flex-1 p-8 border-b md:border-b-0 md:border-r border-[#27272a]">
                    <div className="flex items-center gap-3 mb-8">
                        <span className="material-symbols-outlined text-primary">person_add</span>
                        <div>
                            <h2 className="text-sm font-bold text-white uppercase tracking-wider">Add New User</h2>
                            <p className="text-[10px] text-[#71717a] font-mono uppercase">Provision system credentials</p>
                        </div>
                    </div>

                    {error && (
                        <div className="mb-6 p-3 rounded bg-destructive/10 border border-destructive/20 text-destructive text-[10px] font-mono">
                            {error}
                        </div>
                    )}

                    <div className="space-y-5">
                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Username</label>
                            <input
                                type="text"
                                value={username}
                                onChange={(e) => setUsername(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                placeholder="j.smith"
                            />
                        </div>

                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Email <span className="opacity-50">(Optional)</span></label>
                            <input
                                type="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                placeholder="smith@enterprise.com"
                            />
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Password</label>
                                <div className="relative">
                                    <input
                                        type={showPassword ? "text" : "password"}
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                    />
                                    <button
                                        onClick={() => setShowPassword(!showPassword)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-[#71717a] hover:text-white transition-colors"
                                    >
                                        <span className="material-symbols-outlined text-lg">
                                            {showPassword ? 'visibility_off' : 'visibility'}
                                        </span>
                                    </button>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">Confirm</label>
                                <input
                                    type={showPassword ? "text" : "password"}
                                    value={confirmPassword}
                                    onChange={(e) => setConfirmPassword(e.target.value)}
                                    className="w-full bg-black/40 border border-[#27272a] rounded px-4 py-3 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                                />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <label className="text-[9px] font-bold text-[#71717a] uppercase tracking-widest px-1">System Role</label>
                            <select
                                value={role}
                                onChange={(e) => setRole(e.target.value as UserRole)}
                                className="w-full bg-[#09090b] border border-[#27272a] rounded px-4 py-3 text-xs font-bold text-white focus:border-primary/50 outline-none appearance-none cursor-pointer uppercase tracking-widest"
                            >
                                <option value="Administrator">Administrator</option>
                                <option value="Operator">Operator</option>
                                <option value="Read-Only">Read-Only</option>
                            </select>
                        </div>
                    </div>
                </div>

                {/* Right Side: Permissions */}
                <div className="flex-1 p-8 bg-[#09090b]/50">
                    <h3 className="text-[10px] font-bold text-white uppercase tracking-widest mb-6">Granular Permissions</h3>

                    <div className="space-y-4">
                        {permissions.map((perm) => (
                            <div
                                key={perm.id}
                                onClick={() => handleTogglePermission(perm.id)}
                                className={cn(
                                    "p-4 rounded border cursor-pointer transition-all group",
                                    perm.checked
                                        ? "bg-primary/5 border-primary/30 ring-1 ring-primary/20"
                                        : "bg-black/20 border-[#27272a] hover:border-[#3f3f46]"
                                )}
                            >
                                <div className="flex items-center gap-3">
                                    <div className={cn(
                                        "size-4 rounded-sm border flex items-center justify-center transition-all",
                                        perm.checked ? "bg-primary border-primary text-black" : "border-[#3f3f46]"
                                    )}>
                                        {perm.checked && <span className="material-symbols-outlined text-[10px] font-bold">check</span>}
                                    </div>
                                    <div className="flex-1">
                                        <div className={cn(
                                            "text-[11px] font-bold uppercase tracking-wider transition-colors",
                                            perm.checked ? "text-primary" : "text-[#71717a] group-hover:text-white"
                                        )}>
                                            {perm.label}
                                        </div>
                                        <div className="text-[9px] text-[#71717a] mt-0.5">{perm.description}</div>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>

                    <div className="mt-12 flex items-center justify-end gap-4">
                        <button
                            onClick={onClose}
                            className="px-6 py-2.5 text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-widest transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleCreateUser}
                            disabled={isLoading}
                            className={cn(
                                "px-8 py-2.5 bg-primary hover:bg-green-400 text-black text-[10px] font-bold uppercase tracking-widest rounded transition-all shadow-[0_4px_12px_rgba(25,230,100,0.2)]",
                                isLoading && "opacity-50 cursor-not-allowed"
                            )}
                        >
                            {isLoading ? 'Processing...' : 'Provision User'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}
