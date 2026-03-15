import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { Server, ChevronDown, Check } from 'lucide-react'
import { cn } from '../lib/utils'

interface Library {
    id: string
    display_name: string
    is_default: boolean
}

const LibrarySelector: React.FC = () => {
    const [libraries, setLibraries] = useState<Library[]>([])
    const [activeLibrary, setActiveLibrary] = useState<string | null>(null)
    const [loading, setLoading] = useState(true)
    const [isOpen, setIsOpen] = useState(false)

    useEffect(() => {
        const fetchLibraries = async () => {
            try {
                const res = await api.listLibraries()
                if (res.success && res.data && res.data.data) {
                    setLibraries(res.data.data)
                    const defaultLib = res.data.data.find((l: Library) => l.is_default) || res.data.data[0]
                    if (defaultLib) setActiveLibrary(defaultLib.id)
                }
            } catch (err) {
                console.error('Failed to fetch libraries', err)
            } finally {
                setLoading(false)
            }
        }
        fetchLibraries()
    }, [])

    const handleSelect = (id: string) => {
        setActiveLibrary(id)
        setIsOpen(false)
        // In a real app, this might trigger a context update or a page reload
        // For now, we just update the local active state
    }

    if (loading) return null
    if (libraries.length <= 1) return null

    const currentLib = libraries.find(l => l.id === activeLibrary) || libraries[0]

    return (
        <div className="relative">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-3 bg-[#121214] border border-[#27272a] hover:border-[#3f3f46] px-4 py-2 rounded transition-all group"
            >
                <Server className="size-4 text-primary" />
                <div className="text-left">
                    <div className="text-[8px] font-bold text-[#71717a] uppercase tracking-widest leading-none mb-1">Active Library</div>
                    <div className="text-[10px] font-bold text-white uppercase tracking-wider flex items-center gap-2">
                        {currentLib.display_name}
                        <ChevronDown className={cn("size-3 text-[#3f3f46] group-hover:text-white transition-all", isOpen && "rotate-180")} />
                    </div>
                </div>
            </button>

            {isOpen && (
                <div className="absolute top-full left-0 mt-2 w-64 bg-[#121214] border border-[#27272a] rounded-lg shadow-2xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-200">
                    <div className="p-2 border-b border-[#27272a] bg-[#18181b]">
                        <span className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] px-2">Configured Topology</span>
                    </div>
                    <div className="py-1">
                        {libraries.map((lib) => (
                            <button
                                key={lib.id}
                                onClick={() => handleSelect(lib.id)}
                                className={cn(
                                    "w-full flex items-center justify-between px-4 py-3 text-left hover:bg-white/5 transition-colors group",
                                    activeLibrary === lib.id ? "bg-primary/5" : ""
                                )}
                            >
                                <div className="flex items-center gap-3">
                                    <Server className={cn("size-4", activeLibrary === lib.id ? "text-primary" : "text-[#3f3f46] group-hover:text-white")} />
                                    <div>
                                        <div className="text-[10px] font-bold text-white uppercase tracking-wider">{lib.display_name}</div>
                                        <div className="text-[8px] font-mono text-[#71717a] mt-0.5">{lib.id}</div>
                                    </div>
                                </div>
                                {activeLibrary === lib.id && <Check className="size-3 text-primary" />}
                            </button>
                        ))}
                    </div>
                    <div className="p-3 bg-black/40 border-t border-[#27272a]">
                        <button className="w-full py-2 bg-primary/10 border border-primary/20 text-primary hover:bg-primary/20 rounded text-[9px] font-bold uppercase tracking-widest transition-all">
                            Manage Libraries
                        </button>
                    </div>
                </div>
            )}
        </div>
    )
}

export default LibrarySelector
