import React, { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { FileText, Download, Trash2, CheckCircle, AlertCircle, Clock } from 'lucide-react'

interface DiagnosticReport {
    id: number
    status: string
    summary: string
    created_at: string
    report_json_path?: string
    report_text_path?: string
}

const DiagnosticsHistory: React.FC = () => {
    const [reports, setReports] = useState<DiagnosticReport[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const fetchReports = async () => {
        setLoading(true)
        try {
            const res = await api.getDiagnosticsReports()
            if (res.success && res.data) {
                setReports(res.data.reports)
            } else {
                setError(res.error || 'Failed to fetch reports')
            }
        } catch (err) {
            setError('Network error')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        fetchReports()
    }, [])

    const handleDelete = async (id: number) => {
        if (!window.confirm('Are you sure you want to delete this report?')) return

        try {
            const res = await api.deleteDiagnosticsReport(id)
            if (res.success) {
                setReports(reports.filter(r => r.id !== id))
            } else {
                alert('Failed to delete report: ' + res.error)
            }
        } catch (err) {
            alert('Network error')
        }
    }

    const formatDate = (dateStr: string) => {
        try {
            return new Intl.DateTimeFormat('en-US', {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            }).format(new Date(dateStr))
        } catch (e) {
            return dateStr
        }
    }

    if (loading) return <div className="p-4 text-gray-400">Loading diagnostic history...</div>
    if (error) return <div className="p-4 text-red-400">Error: {error}</div>

    return (
        <div className="bg-[#1a1a1a] border border-white/10 rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-white/10 bg-white/5 flex items-center justify-between">
                <h3 className="text-sm font-medium flex items-center gap-2">
                    <FileText className="w-4 h-4 text-blue-400" />
                    Diagnostic Report History
                </h3>
                <button
                    onClick={fetchReports}
                    className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                >
                    Refresh
                </button>
            </div>

            <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                    <thead>
                        <tr className="bg-white/5 text-gray-400">
                            <th className="px-4 py-2 font-medium">Date</th>
                            <th className="px-4 py-2 font-medium">Status</th>
                            <th className="px-4 py-2 font-medium">Summary</th>
                            <th className="px-4 py-2 font-medium text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                        {reports.length === 0 ? (
                            <tr>
                                <td colSpan={4} className="px-4 py-8 text-center text-gray-500">
                                    No historical reports found.
                                </td>
                            </tr>
                        ) : (
                            reports.map((report) => (
                                <tr key={report.id} className="hover:bg-white/5 transition-colors">
                                    <td className="px-4 py-3 text-gray-300 whitespace-nowrap">
                                        {formatDate(report.created_at)}
                                    </td>
                                    <td className="px-4 py-3">
                                        <div className="flex items-center gap-1.5">
                                            {report.status === 'completed' || report.status === 'success' ? (
                                                <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                                            ) : report.status === 'failed' || report.status === 'error' ? (
                                                <AlertCircle className="w-3.5 h-3.5 text-red-500" />
                                            ) : (
                                                <Clock className="w-3.5 h-3.5 text-yellow-500" />
                                            )}
                                            <span className="capitalize">{report.status}</span>
                                        </div>
                                    </td>
                                    <td className="px-4 py-3 text-gray-400 max-w-xs truncate">
                                        {report.summary || 'None'}
                                    </td>
                                    <td className="px-4 py-3 text-right">
                                        <div className="flex items-center justify-end gap-2">
                                            {report.id && (
                                                <>
                                                    <a
                                                        href={`/api/diagnostics/reports/${report.id}/download?kind=json&token=${localStorage.getItem('session_token') || ''}`}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="p-1.5 hover:bg-white/10 rounded text-blue-400 transition-colors"
                                                        title="Download JSON"
                                                    >
                                                        <Download className="w-3.5 h-3.5" />
                                                    </a>
                                                    <button
                                                        onClick={() => handleDelete(report.id)}
                                                        className="p-1.5 hover:bg-red-500/20 rounded text-red-400 transition-colors"
                                                        title="Delete Report"
                                                    >
                                                        <Trash2 className="w-3.5 h-3.5" />
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

export default DiagnosticsHistory
