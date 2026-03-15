import React, { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { useDemoMode } from "@/lib/demoMode";
import { Loader2, Trash2, Shield, Plus, Activity, CheckCircle2, AlertTriangle, Link as LinkIcon, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import ConfirmationModal from "@/components/ui/ConfirmationModal";

const EVENT_SOURCES = [
    { id: 'JOB_COMPLETED', label: 'Job Completed', icon: CheckCircle2, color: 'text-green-400' },
    { id: 'JOB_FAILED', label: 'Job Failed', icon: AlertTriangle, color: 'text-red-400' },
    { id: 'JOB_CANCELLED', label: 'Job Cancelled', icon: Activity, color: 'text-yellow-400' },
    { id: 'TAPE_ALERT', label: 'Hardware Alert', icon: Shield, color: 'text-orange-400' },
    { id: 'COMPLIANCE_ALARM', label: 'Compliance Violation', icon: Lock, color: 'text-purple-400' }
];

export default function WebhooksPanel() {
    const { isDemoMode } = useDemoMode();
    const [webhooks, setWebhooks] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [isAdding, setIsAdding] = useState(false);

    // Form state
    const [name, setName] = useState('');
    const [url, setUrl] = useState('');
    const [secret, setSecret] = useState('');
    const [selectedEvents, setSelectedEvents] = useState<string[]>(EVENT_SOURCES.map(e => e.id));
    const [submitting, setSubmitting] = useState(false);
    const [testing, setTesting] = useState(false);

    const [confirmDelete, setConfirmDelete] = useState<{ isOpen: boolean; id: number | null }>({ isOpen: false, id: null });

    useEffect(() => {
        fetchWebhooks();
    }, []);

    const fetchWebhooks = async () => {
        if (isDemoMode) {
            setWebhooks([
                { id: 1, name: 'Discord Alerts', url: 'https://discord.com/api/webhooks/...', event_types: ['JOB_FAILED', 'TAPE_ALERT'], created_at: new Date().toISOString() },
                { id: 2, name: 'Slack Logging', url: 'https://hooks.slack.com/services/...', event_types: ['JOB_COMPLETED', 'JOB_FAILED'], created_at: new Date().toISOString() }
            ]);
            setLoading(false);
            return;
        }

        setLoading(true);
        try {
            const res = await api.getWebhooks();
            if (res.success && res.data) {
                setWebhooks(res.data.webhooks);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const res = await api.createWebhook({
                name,
                url,
                secret,
                event_types: selectedEvents
            });
            if (res.success) {
                setIsAdding(false);
                setName('');
                setUrl('');
                setSecret('');
                fetchWebhooks();
            } else {
                alert(`Error: ${res.error}`);
            }
        } catch (e) {
            alert('Failed to create webhook');
        } finally {
            setSubmitting(false);
        }
    };

    const handleDelete = async () => {
        if (!confirmDelete.id) return;
        try {
            const res = await api.deleteWebhook(confirmDelete.id);
            if (res.success) {
                fetchWebhooks();
            }
        } catch (e) {
            console.error(e);
        } finally {
            setConfirmDelete({ isOpen: false, id: null });
        }
    };

    const handleTest = async () => {
        setTesting(true);
        try {
            const res = await api.testWebhook({ url, secret });
            if (res.success) {
                alert(`Success! Terminal responded with status ${res.data?.status}`);
            } else {
                alert(`Test failed: ${res.error}`);
            }
        } catch (e) {
            alert('Network error testing webhook');
        } finally {
            setTesting(false);
        }
    };

    const toggleEvent = (eventId: string) => {
        if (selectedEvents.includes(eventId)) {
            setSelectedEvents(prev => prev.filter(id => id !== eventId));
        } else {
            setSelectedEvents(prev => [...prev, eventId]);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center p-12">
                <Loader2 className="animate-spin text-primary size-8" />
            </div>
        );
    }

    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <header className="flex items-center justify-between">
                <div>
                    <h3 className="text-xs font-bold text-white uppercase tracking-[0.25em]">Workflow Automations</h3>
                    <p className="text-[10px] text-[#71717a] mt-1 font-bold uppercase tracking-wider">Configure real-time event notifications via secure webhooks</p>
                </div>
                {!isAdding && (
                    <button
                        onClick={() => setIsAdding(true)}
                        className="px-4 py-2 bg-primary/10 hover:bg-primary/20 text-primary text-[10px] font-bold uppercase tracking-widest rounded border border-primary/20 transition-all flex items-center gap-2"
                    >
                        <Plus className="size-3" />
                        Add Webhook
                    </button>
                )}
            </header>

            {isAdding && (
                <div className="bg-[#121214] border border-primary/30 rounded-lg p-6 space-y-6 shadow-[0_15px_40px_rgba(0,0,0,0.5)] border-white/10 animate-in slide-in-from-top-4 duration-300">
                    <div className="grid grid-cols-2 gap-6">
                        <div className="space-y-4">
                            <div>
                                <label className="block text-[10px] font-bold text-[#71717a] uppercase tracking-widest mb-2">Endpoint Name</label>
                                <input
                                    type="text"
                                    value={name}
                                    onChange={e => setName(e.target.value)}
                                    placeholder="e.g. SOC Integration"
                                    className="w-full bg-[#0c0c0e] border border-[#27272a] rounded px-4 py-2 text-xs text-white focus:border-primary/50 transition-colors"
                                />
                            </div>
                            <div>
                                <label className="block text-[10px] font-bold text-[#71717a] uppercase tracking-widest mb-2">Target URL</label>
                                <input
                                    type="url"
                                    value={url}
                                    onChange={e => setUrl(e.target.value)}
                                    placeholder="https://"
                                    className="w-full bg-[#0c0c0e] border border-[#27272a] rounded px-4 py-2 text-xs text-white focus:border-primary/50 transition-colors"
                                />
                            </div>
                            <div>
                                <label className="block text-[10px] font-bold text-[#71717a] uppercase tracking-widest mb-2">Signing Secret (HMAC-SHA256)</label>
                                <div className="relative">
                                    <input
                                        type="password"
                                        value={secret}
                                        onChange={e => setSecret(e.target.value)}
                                        placeholder="Optional HMAC Secret"
                                        className="w-full bg-[#0c0c0e] border border-[#27272a] rounded px-4 py-2 pl-10 text-xs text-white focus:border-primary/50 transition-colors"
                                    />
                                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-[#3f3f46]" />
                                </div>
                            </div>
                        </div>

                        <div>
                            <label className="block text-[10px] font-bold text-[#71717a] uppercase tracking-widest mb-4">Event Subscription</label>
                            <div className="grid grid-cols-1 gap-2">
                                {EVENT_SOURCES.map(event => (
                                    <button
                                        key={event.id}
                                        onClick={() => toggleEvent(event.id)}
                                        className={cn(
                                            "flex items-center justify-between p-3 rounded border text-left transition-all",
                                            selectedEvents.includes(event.id)
                                                ? "bg-primary/5 border-primary/30"
                                                : "bg-transparent border-[#27272a] opacity-50 hover:opacity-100"
                                        )}
                                    >
                                        <div className="flex items-center gap-3">
                                            <event.icon className={cn("size-4", event.color)} />
                                            <div>
                                                <div className="text-[10px] font-bold text-white uppercase tracking-wider">{event.label}</div>
                                                <div className="text-[8px] text-[#71717a] font-mono">{event.id}</div>
                                            </div>
                                        </div>
                                        {selectedEvents.includes(event.id) && <Plus className="size-3 text-primary rotate-45" />}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center justify-end gap-3 pt-4 border-t border-[#27272a]">
                        <button
                            onClick={() => setIsAdding(false)}
                            className="px-4 py-2 text-[10px] font-bold text-[#71717a] uppercase tracking-widest hover:text-white transition-colors"
                        >
                            Cancel
                        </button>
                        <button
                            onClick={handleTest}
                            disabled={!url || testing}
                            className="px-4 py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white text-[10px] font-bold uppercase tracking-widest rounded border border-[#3f3f46] transition-all flex items-center gap-2 disabled:opacity-50"
                        >
                            {testing ? <Loader2 className="animate-spin size-3" /> : <Activity className="size-3" />}
                            Test Connector
                        </button>
                        <button
                            onClick={handleCreate}
                            disabled={submitting || !url}
                            className="px-6 py-2 bg-primary hover:bg-primary/80 text-black text-[10px] font-bold uppercase tracking-widest rounded transition-all disabled:opacity-50 shadow-[0_5px_15px_rgba(var(--primary-rgb),0.3)]"
                        >
                            {submitting ? <Loader2 className="animate-spin size-3" /> : 'Activate Webhook'}
                        </button>
                    </div>
                </div>
            )}

            <div className="grid grid-cols-1 gap-4">
                {(!webhooks || webhooks.length === 0) ? (
                    <div className="border border-dashed border-[#27272a] rounded-lg p-12 text-center">
                        <div className="size-16 rounded-full bg-[#18181b] border border-[#27272a] flex items-center justify-center mx-auto mb-4">
                            <LinkIcon className="size-8 text-[#3f3f46]" />
                        </div>
                        <h4 className="text-[11px] font-bold text-white uppercase tracking-widest">No Active Automations</h4>
                        <p className="text-[10px] text-[#71717a] mt-2 max-w-xs mx-auto">Webhooks enable your FossilSafe appliance to talk to your SIEM, chat ops, or custom recovery scripts.</p>
                    </div>
                ) : (
                    webhooks.map(webhook => {
                        let eventTypes: string[] = [];
                        try {
                            eventTypes = Array.isArray(webhook.event_types)
                                ? webhook.event_types
                                : (typeof webhook.event_types === 'string'
                                    ? JSON.parse(webhook.event_types || '[]')
                                    : []);
                        } catch (e) {
                            console.error("Failed to parse event_types", e);
                        }

                        return (
                            <div key={webhook.id} className="bg-[#121214] border border-[#27272a] hover:border-white/10 rounded-lg p-5 flex items-center justify-between group transition-all">
                                <div className="flex items-center gap-4">
                                    <div className="size-10 rounded bg-[#0c0c0e] border border-[#27272a] flex items-center justify-center text-primary">
                                        <Activity className="size-5" />
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <h4 className="text-[11px] font-bold text-white uppercase tracking-widest">{webhook.name || 'Untitled Webhook'}</h4>
                                            <span className="text-[8px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-500 font-bold border border-green-500/20 uppercase">Active</span>
                                        </div>
                                        <p className="text-[10px] font-mono text-[#3f3f46] truncate max-w-sm mt-1">{webhook.url}</p>
                                        <div className="flex items-center gap-2 mt-2">
                                            {Array.isArray(eventTypes) && eventTypes.map((e: string) => (
                                                <span key={e} className="text-[8px] font-bold text-[#71717a] uppercase border border-[#27272a] px-1.5 py-0.5 rounded">{e}</span>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button
                                        onClick={() => setConfirmDelete({ isOpen: true, id: webhook.id })}
                                        className="p-2 hover:bg-red-500/10 hover:text-red-500 text-[#3f3f46] rounded transition-all"
                                    >
                                        <Trash2 className="size-4" />
                                    </button>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>

            <ConfirmationModal
                isOpen={confirmDelete.isOpen}
                onClose={() => setConfirmDelete({ isOpen: false, id: null })}
                onConfirm={handleDelete}
                title="Delete Webhook"
                message="Are you sure you want to delete this webhook automation? External systems will no longer receive event notifications."
                variant="danger"
                confirmText="Delete Policy"
            />
        </div>
    );
}
