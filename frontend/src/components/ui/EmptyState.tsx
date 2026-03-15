import { cn } from '@/lib/utils';
import { LucideIcon } from 'lucide-react';

interface EmptyStateProps {
    icon?: LucideIcon;
    title: string;
    message?: string;
    action?: () => void;
    actionLabel?: string;
    className?: string;
}

export function EmptyState({
    icon: Icon,
    title,
    message,
    action,
    actionLabel,
    className
}: EmptyStateProps) {
    return (
        <div className={cn("flex items-center justify-center h-64 border border-dashed border-[#27272a] rounded-lg bg-[#121214]/50 p-6", className)}>
            <div className="flex flex-col items-center gap-2 text-center max-w-sm">
                {Icon && <Icon className="size-10 text-muted-foreground mb-2 opacity-50" />}
                <span className="text-[#71717a] font-bold text-sm uppercase tracking-wider">{title}</span>
                {message && <p className="text-[#71717a] text-xs font-mono">{message}</p>}
                {action && actionLabel && (
                    <button
                        onClick={action}
                        className="mt-4 text-xs text-primary hover:underline uppercase tracking-wide font-bold"
                    >
                        {actionLabel}
                    </button>
                )}
            </div>
        </div>
    );
}
