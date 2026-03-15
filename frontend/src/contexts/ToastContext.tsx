import { createContext, useContext, useState, ReactNode } from 'react';
import { cn } from '@/lib/utils';

type ToastType = 'success' | 'error' | 'info';

interface Toast {
    id: string;
    message: string;
    type: ToastType;
}

interface ToastContextType {
    toasts: Toast[];
    showToast: (message: string, type: ToastType) => void;
    removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const ToastProvider = ({ children }: { children: ReactNode }) => {
    const [toasts, setToasts] = useState<Toast[]>([]);

    const showToast = (message: string, type: 'success' | 'error' | 'info' = 'info') => {
        const id = Math.random().toString(36).substring(2, 9);
        setToasts((prev) => [...prev, { id, message, type }]);
        setTimeout(() => removeToast(id), 5000);
    };

    const removeToast = (id: string) => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
    };

    return (
        <ToastContext.Provider value={{ toasts, showToast, removeToast }}>
            {children}
            <div className="fixed bottom-4 right-4 z-[200] flex flex-col gap-2 pointer-events-none">
                {toasts.map((toast) => (
                    <div
                        key={toast.id}
                        className={cn(
                            "px-4 py-3 rounded shadow-lg text-sm font-bold flex items-center gap-2 min-w-[300px] animate-in slide-in-from-right fade-in duration-300 pointer-events-auto",
                            toast.type === 'success' ? "bg-[#18181b] border border-primary/30 text-white" :
                                toast.type === 'error' ? "bg-[#18181b] border border-destructive/30 text-white" :
                                    "bg-[#18181b] border border-white/10 text-white"
                        )}
                    >
                        {toast.type === 'success' && <span className="material-symbols-outlined text-primary text-sm">check_circle</span>}
                        {toast.type === 'error' && <span className="material-symbols-outlined text-destructive text-sm">error</span>}
                        {toast.type === 'info' && <span className="material-symbols-outlined text-blue-400 text-sm">info</span>}
                        <span className="font-mono tracking-tight">{toast.message}</span>
                    </div>
                ))}
            </div>
        </ToastContext.Provider>
    );
};

export const useToast = () => {
    const context = useContext(ToastContext);
    if (!context) {
        throw new Error('useToast must be used within a ToastProvider');
    }
    return context;
};
