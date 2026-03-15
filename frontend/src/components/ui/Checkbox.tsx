import React from "react"
import { cn } from "@/lib/utils"
import { Check } from "lucide-react"

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'onChange'> {
    checked: boolean
    onChange: (checked: boolean) => void
    label?: string
}

export function Checkbox({ checked, onChange, label, className, ...props }: CheckboxProps) {
    return (
        <label className={cn("inline-flex items-center gap-2 cursor-pointer group", className)}>
            <div className="relative">
                <input
                    type="checkbox"
                    className="sr-only"
                    checked={checked}
                    onChange={(e) => onChange(e.target.checked)}
                    {...props}
                />
                <div className={cn(
                    "size-4 rounded border transition-all duration-200 flex items-center justify-center",
                    checked
                        ? "bg-primary border-primary shadow-[0_0_10px_rgba(25,230,100,0.3)]"
                        : "bg-black/40 border-[#27272a] group-hover:border-[#3f3f46]"
                )}>
                    {checked && <Check className="size-3 text-black stroke-[3px]" />}
                </div>
            </div>
            {label && <span className="text-[10px] font-bold text-[#71717a] uppercase tracking-wider group-hover:text-white transition-colors">{label}</span>}
        </label>
    )
}
