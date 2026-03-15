import { ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';

interface ErrorStateProps {
    title?: string;
    message: string;
    retry?: () => void;
    retryLabel?: string;
    className?: string;
}

export function ErrorState({
    title = 'Something went wrong',
    message,
    retry,
    retryLabel = 'Retry',
    className
}: ErrorStateProps) {
    return (
        <div className={cn("flex items-center justify-center h-64 border border-destructive/20 bg-destructive/5 rounded-lg p-6", className)}>
            <div className="flex flex-col items-center gap-2 text-center max-w-md">
                <ShieldAlert className="size-10 text-destructive mb-2" />
                <span className="text-white font-bold text-lg">{title}</span>
                <span className="text-muted-foreground text-sm font-mono">{message}</span>
                {retry && (
                    <button
                        onClick={retry}
                        className="mt-4 px-4 py-2 bg-destructive/10 border border-destructive/30 text-destructive hover:bg-destructive hover:text-white rounded text-xs font-bold transition-all uppercase tracking-wider"
                    >
                        {retryLabel}
                    </button>
                )}
            </div>
        </div>
    );
}
