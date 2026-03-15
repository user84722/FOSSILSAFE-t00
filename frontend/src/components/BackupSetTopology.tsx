import { useRef, useEffect, useState } from "react"
import { GraphData, GraphNode } from "@/lib/api"

interface BackupSetTopologyProps {
    data: GraphData
}

export default function BackupSetTopology({ data }: BackupSetTopologyProps) {
    const containerRef = useRef<HTMLDivElement>(null)
    const [dimensions, setDimensions] = useState({ width: 0, height: 0 })
    const [hoveredNode, setHoveredNode] = useState<string | null>(null)

    useEffect(() => {
        if (!containerRef.current) return

        const updateDimensions = () => {
            if (containerRef.current) {
                setDimensions({
                    width: containerRef.current.clientWidth,
                    height: containerRef.current.clientHeight
                })
            }
        }

        updateDimensions()
        window.addEventListener('resize', updateDimensions)
        return () => window.removeEventListener('resize', updateDimensions)
    }, [])

    const { nodes, edges } = data

    // Simple layered layout
    const layers: Record<string, GraphNode[]> = {
        set: nodes.filter(n => n.type === 'set'),
        snapshot: nodes.filter(n => n.type === 'snapshot'),
        tape: nodes.filter(n => n.type === 'tape')
    }

    const nodePositions: Record<string, { x: number, y: number }> = {}

    // Calculation constants
    const padding = 60
    const layerSpacing = (dimensions.height - padding * 2) / 2

    // Position "set" nodes (usually just 1)
    layers.set.forEach((node, i) => {
        const x = dimensions.width / 2 + (i - (layers.set.length - 1) / 2) * 200
        nodePositions[node.id] = { x, y: padding }
    })

    // Position "snapshot" nodes
    layers.snapshot.forEach((node, i) => {
        const x = (dimensions.width / (layers.snapshot.length + 1)) * (i + 1)
        nodePositions[node.id] = { x, y: padding + layerSpacing }
    })

    // Position "tape" nodes
    layers.tape.forEach((node, i) => {
        const x = (dimensions.width / (layers.tape.length + 1)) * (i + 1)
        nodePositions[node.id] = { x, y: padding + layerSpacing * 2 }
    })

    if (dimensions.width === 0) return null

    return (
        <div ref={containerRef} className="w-full h-full relative cursor-crosshair overflow-hidden">
            <svg width={dimensions.width} height={dimensions.height} className="absolute inset-0">
                {/* Defs for gradients and glows */}
                <defs>
                    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                        <feGaussianBlur stdDeviation="3" result="blur" />
                        <feComposite in="SourceGraphic" in2="blur" operator="over" />
                    </filter>
                    <linearGradient id="edgeGradient" x1="0%" y1="0%" x2="0%" y2="100%">
                        <stop offset="0%" stopColor="#19e664" stopOpacity="0.6" />
                        <stop offset="100%" stopColor="#19e664" stopOpacity="0.2" />
                    </linearGradient>
                </defs>

                {/* Edges */}
                {edges.map(edge => {
                    const start = nodePositions[edge.source]
                    const end = nodePositions[edge.target]
                    if (!start || !end) return null

                    const isHighlighted = hoveredNode === edge.source || hoveredNode === edge.target

                    return (
                        <g key={edge.id}>
                            <path
                                d={`M ${start.x} ${start.y} C ${start.x} ${(start.y + end.y) / 2}, ${end.x} ${(start.y + end.y) / 2}, ${end.x} ${end.y}`}
                                stroke={isHighlighted ? "#19e664" : "#27272a"}
                                strokeWidth={isHighlighted ? 2 : 1}
                                fill="none"
                                className="transition-all duration-300"
                            />
                        </g>
                    )
                })}

                {/* Nodes */}
                {nodes.map(node => {
                    const pos = nodePositions[node.id]
                    if (!pos) return null

                    const isHovered = hoveredNode === node.id

                    let nodeColor = "#71717a"

                    if (node.type === 'set') nodeColor = "#19e664"
                    if (node.type === 'snapshot') nodeColor = "#ffffff"
                    if (node.type === 'tape') {
                        nodeColor = node.trust === 'verified' ? "#19e664" : node.trust === 'online' ? "#3b82f6" : "#71717a"
                    }

                    return (
                        <g
                            key={node.id}
                            transform={`translate(${pos.x}, ${pos.y})`}
                            onMouseEnter={() => setHoveredNode(node.id)}
                            onMouseLeave={() => setHoveredNode(null)}
                            className="cursor-pointer"
                        >
                            {/* Glow effect on hover */}
                            {isHovered && (
                                <circle r="25" fill={nodeColor} opacity="0.15" filter="url(#glow)" />
                            )}

                            {/* Main circle */}
                            <circle
                                r={node.type === 'set' ? 12 : 8}
                                fill="#121214"
                                stroke={nodeColor}
                                strokeWidth={isHovered ? 3 : 2}
                                className="transition-all duration-300"
                            />

                            {/* Inner point */}
                            <circle r="3" fill={nodeColor} />

                            {/* Label */}
                            <text
                                y={node.type === 'set' ? -25 : 25}
                                textAnchor="middle"
                                fill={isHovered ? "white" : "#71717a"}
                                className="text-[10px] font-mono font-bold uppercase tracking-wider select-none transition-colors"
                            >
                                {node.label}
                            </text>

                            {/* Type metadata */}
                            {isHovered && (
                                <g transform="translate(0, 40)">
                                    <rect
                                        x="-40"
                                        y="-10"
                                        width="80"
                                        height="20"
                                        rx="4"
                                        fill="#18181b"
                                        stroke="#27272a"
                                    />
                                    <text
                                        textAnchor="middle"
                                        dy="4"
                                        fill="#a1a1aa"
                                        className="text-[8px] font-mono uppercase tracking-widest"
                                    >
                                        {node.type}
                                    </text>
                                </g>
                            )}
                        </g>
                    )
                })}
            </svg>

            {/* Legend */}
            <div className="absolute bottom-6 right-6 bg-black/60 backdrop-blur-md border border-[#27272a] p-4 rounded flex flex-col gap-3">
                <div className="text-[9px] font-bold text-[#71717a] uppercase tracking-[0.2em] mb-1">Status_Legend</div>
                <div className="flex items-center gap-3">
                    <div className="size-2 rounded-full bg-primary shadow-[0_0_8px_rgba(25,230,100,0.5)]"></div>
                    <span className="text-[9px] font-mono text-white uppercase tracking-widest">Verified_Medium</span>
                </div>
                <div className="flex items-center gap-3">
                    <div className="size-2 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]"></div>
                    <span className="text-[9px] font-mono text-white uppercase tracking-widest">Online_Medium</span>
                </div>
                <div className="flex items-center gap-3">
                    <div className="size-2 rounded-full bg-[#71717a]"></div>
                    <span className="text-[9px] font-mono text-white uppercase tracking-widest">Unknown_State</span>
                </div>
            </div>

            {/* Scale indicator */}
            <div className="absolute top-6 left-6 text-[9px] font-mono text-[#71717a] uppercase tracking-[0.3em] flex flex-col gap-1">
                <span>[Topology_Map_v1.0]</span>
                <span className="text-primary/60">Logical_Chain_Verified</span>
            </div>
        </div>
    )
}
