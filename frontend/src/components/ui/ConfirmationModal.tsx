

interface ConfirmationModalProps {
    isOpen: boolean
    onClose: () => void
    onConfirm: () => void
    title: string
    message: string
    confirmText?: string
    cancelText?: string
    variant?: 'danger' | 'info' | 'warning'
}

export default function ConfirmationModal({
    isOpen,
    onClose,
    onConfirm,
    title,
    message,
    confirmText = 'Confirm',
    cancelText = 'Cancel',
    variant = 'info'
}: ConfirmationModalProps) {
    if (!isOpen) return null

    const variantStyles = {
        danger: {
            icon: 'warning',
            iconColor: 'text-destructive',
            buttonBg: 'bg-destructive hover:bg-red-500',
            borderColor: 'border-destructive/20',
            glow: 'shadow-[0_0_20px_rgba(220,38,38,0.1)]'
        },
        warning: {
            icon: 'report_problem',
            iconColor: 'text-amber-500',
            buttonBg: 'bg-amber-500 hover:bg-amber-400 text-black',
            borderColor: 'border-amber-500/20',
            glow: 'shadow-[0_0_20px_rgba(245,158,11,0.05)]'
        },
        info: {
            icon: 'info',
            iconColor: 'text-primary',
            buttonBg: 'bg-primary hover:bg-green-400 text-black',
            borderColor: 'border-primary/20',
            glow: 'shadow-[0_0_20px_rgba(25,230,100,0.1)]'
        }
    }

    const style = variantStyles[variant]

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
            <div className={`relative w-full max-w-md bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200 ${style.glow}`}>
                <div className="p-6">
                    <div className="flex items-start gap-4">
                        <div className={`p-2 rounded bg-black/40 border ${style.borderColor} ${style.iconColor}`}>
                            <span className="material-symbols-outlined text-xl">{style.icon}</span>
                        </div>
                        <div className="flex-1">
                            <h3 className="text-sm font-bold text-white uppercase tracking-[0.2em] mb-2">{title}</h3>
                            <p className="text-xs text-[#71717a] leading-relaxed">
                                {message}
                            </p>
                        </div>
                    </div>
                </div>

                <div className="flex items-center justify-end gap-3 px-6 py-4 bg-black/20 border-t border-[#27272a]">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-[#71717a] hover:text-white text-[10px] font-bold uppercase tracking-[0.2em] transition-colors"
                    >
                        {cancelText}
                    </button>
                    <button
                        onClick={() => {
                            onConfirm()
                            onClose()
                        }}
                        className={`px-6 py-2 rounded text-[10px] font-bold uppercase tracking-[0.2em] transition-all ${style.buttonBg}`}
                    >
                        {confirmText}
                    </button>
                </div>
            </div>
        </div>
    )
}
