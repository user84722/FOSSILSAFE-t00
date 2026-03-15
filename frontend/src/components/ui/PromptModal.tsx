import { useState } from 'react'

interface PromptModalProps {
    isOpen: boolean
    onClose: () => void
    onConfirm: (value: string) => void
    title: string
    message: string
    placeholder?: string
    confirmText?: string
    cancelText?: string
    variant?: 'danger' | 'info'
}

export default function PromptModal({
    isOpen,
    onClose,
    onConfirm,
    title,
    message,
    placeholder = '',
    confirmText = 'Submit',
    cancelText = 'Cancel',
    variant = 'info'
}: PromptModalProps) {
    const [value, setValue] = useState('')

    if (!isOpen) return null

    const variantStyles = {
        danger: {
            icon: 'warning',
            iconColor: 'text-destructive',
            buttonBg: 'bg-destructive hover:bg-red-500',
            borderColor: 'border-destructive/20',
            glow: 'shadow-[0_0_20px_rgba(220,38,38,0.1)]'
        },
        info: {
            icon: 'help',
            iconColor: 'text-primary',
            buttonBg: 'bg-primary hover:bg-green-400 text-black',
            borderColor: 'border-primary/20',
            glow: 'shadow-[0_0_20px_rgba(25,230,100,0.1)]'
        }
    }

    const style = variantStyles[variant]

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault()
        onConfirm(value)
        setValue('')
        onClose()
    }

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
            <form
                onSubmit={handleSubmit}
                className={`relative w-full max-w-md bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200 ${style.glow}`}
            >
                <div className="p-6">
                    <div className="flex items-start gap-4">
                        <div className={`p-2 rounded bg-black/40 border ${style.borderColor} ${style.iconColor}`}>
                            <span className="material-symbols-outlined text-xl">{style.icon}</span>
                        </div>
                        <div className="flex-1">
                            <h3 className="text-sm font-bold text-white uppercase tracking-[0.2em] mb-2">{title}</h3>
                            <p className="text-xs text-[#71717a] leading-relaxed mb-4">
                                {message}
                            </p>
                            <input
                                autoFocus
                                type="text"
                                value={value}
                                onChange={(e) => setValue(e.target.value)}
                                placeholder={placeholder}
                                className="w-full bg-black/40 border border-[#27272a] rounded px-3 py-2 text-sm font-mono text-white focus:border-primary/50 outline-none transition-all"
                            />
                        </div>
                    </div>
                </div>

                <div className="flex items-center justify-end gap-3 px-6 py-4 bg-black/20 border-t border-[#27272a]">
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-4 py-2 text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-[0.2em] transition-colors"
                    >
                        {cancelText}
                    </button>
                    <button
                        type="submit"
                        className={`px-6 py-2 rounded text-[10px] font-bold uppercase tracking-[0.2em] transition-all ${style.buttonBg}`}
                    >
                        {confirmText}
                    </button>
                </div>
            </form>
        </div>
    )
}
