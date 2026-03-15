import React, { useState, useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import { Upload, X, File, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';


interface BrowserUploadStepProps {
    groupId: string;
    onUploadComplete: (resultPaths: string[]) => void;
}

interface UploadFile {
    file: File;
    id: string;
    progress: number;
    status: 'pending' | 'uploading' | 'completed' | 'error';
    sessionId?: string;
    stagingPath?: string;
    error?: string;
}

export function BrowserUploadStep({ groupId, onUploadComplete }: BrowserUploadStepProps) {
    const [files, setFiles] = useState<UploadFile[]>([]);

    const [uploading, setUploading] = useState(false);

    const onDrop = useCallback((acceptedFiles: File[]) => {
        const newFiles = acceptedFiles.map(file => ({
            file,
            id: Math.random().toString(36).substring(7),
            progress: 0,
            status: 'pending' as const
        }));
        setFiles(prev => [...prev, ...newFiles]);
    }, []);

    // @ts-ignore
    const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop });

    const uploadFile = async (fileObj: UploadFile) => {
        try {
            setFiles(prev => prev.map(f => f.id === fileObj.id ? { ...f, status: 'uploading' } : f));

            // Get Auth Token
            const token = localStorage.getItem('token');

            // Get CSRF Token
            let csrfToken = '';
            try {
                console.log("BrowserUploadStep: Fetching CSRF...");
                const csrfRes = await fetch('/api/csrf-token');
                console.log("BrowserUploadStep: CSRF status:", csrfRes.status);
                const csrfData = await csrfRes.json();
                csrfToken = csrfData.csrf_token;
                console.log("BrowserUploadStep: Got CSRF token:", !!csrfToken);
            } catch (e) {
                console.warn("Failed to fetch CSRF token", e);
            }

            // Construct headers helper
            const getHeaders = (contentType?: string) => {
                const h: Record<string, string> = {};
                if (contentType) h['Content-Type'] = contentType;
                if (token) h['Authorization'] = `Bearer ${token}`;
                if (csrfToken) h['X-CSRFToken'] = csrfToken;
                return h;
            };

            // 1. Create Session
            const sessionRes = await fetch('/api/uploads/session', {
                method: 'POST',
                headers: getHeaders('application/json'),
                body: JSON.stringify({
                    filename: fileObj.file.name,
                    size: fileObj.file.size,
                    total_chunks: Math.ceil(fileObj.file.size / (10 * 1024 * 1024)), // 10MB chunks
                    group_id: groupId
                })
            });

            if (sessionRes.status === 405) throw new Error("API Method Not Allowed (405). Check backend permissions/routes.");
            if (sessionRes.status === 401) throw new Error("Unauthorized");

            const sessionData = await sessionRes.json();
            if (!sessionData.success) throw new Error(sessionData.error);
            const sessionId = sessionData.data.session_id;

            // 2. Upload Chunks
            const CHUNK_SIZE = 10 * 1024 * 1024; // 10MB
            const totalChunks = Math.ceil(fileObj.file.size / CHUNK_SIZE);

            for (let i = 0; i < totalChunks; i++) {
                const start = i * CHUNK_SIZE;
                const end = Math.min(start + CHUNK_SIZE, fileObj.file.size);
                const chunk = fileObj.file.slice(start, end);

                const formData = new FormData();
                formData.append('chunk', chunk);
                formData.append('chunk_index', i.toString());

                const chunkRes = await fetch(`/api/uploads/chunk/${sessionId}`, {
                    method: 'POST',
                    headers: getHeaders(), // No Content-Type for FormData (browser sets it)
                    body: formData
                });

                if (!chunkRes.ok) throw new Error(`Chunk upload failed: ${chunkRes.statusText}`);

                // Update progress
                const progress = Math.round(((i + 1) / totalChunks) * 100);
                setFiles(prev => prev.map(f => f.id === fileObj.id ? { ...f, progress } : f));
            }

            // 3. Finalize
            const finalizeRes = await fetch(`/api/uploads/finalize/${sessionId}`, {
                method: 'POST',
                headers: getHeaders('application/json'),
                body: JSON.stringify({ expected_checksum: '' })
            });
            const finalizeData = await finalizeRes.json();

            if (!finalizeData.success) throw new Error(finalizeData.error);

            const stagingPath = finalizeData.data.staging_path;

            setFiles(prev => prev.map(f => f.id === fileObj.id ? { ...f, status: 'completed', sessionId, stagingPath, progress: 100 } : f));
            return stagingPath;

        } catch (e: any) {
            console.error(e);
            setFiles(prev => prev.map(f => f.id === fileObj.id ? { ...f, status: 'error', error: e.message } : f));
            return null;
        }
    };

    const startUploads = async (retryFiles?: UploadFile[]) => {
        setUploading(true);
        const allPaths: string[] = [];

        // Determine which files to process from the current `files` closure state.
        const filesToProcess = retryFiles || files;

        for (const file of filesToProcess) {
            if (file.status === 'completed') {
                if (file.stagingPath) allPaths.push(file.stagingPath);
                continue;
            }

            if (file.status === 'pending' || file.status === 'error') {
                await uploadFile(file);
            }
        }

        setUploading(false);
        
        // Safety: only trigger completion if ALL files in the entire set are now completed.
        // This ensures the wizard doesn't proceed with a partial or broken set.
        const allCompleted = files.every(f => f.status === 'completed');
        const paths = files.map(f => f.stagingPath).filter((p): p is string => !!p);
        
        if (allCompleted && paths.length > 0) {
            onUploadComplete(paths);
        }
    };

    const handleRetryFailed = () => {
        const failedFiles = files.filter(f => f.status === 'error');
        startUploads(failedFiles);
    };

    const removeFile = (id: string) => {
        if (uploading) return;
        setFiles(prev => prev.filter(f => f.id !== id));
    };

    // Protection against accidental navigation
    React.useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (uploading) {
                e.preventDefault();
                e.returnValue = ''; // Chrome requires returnValue to be set
            }
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [uploading]);

    return (
        <div className="space-y-4">
            <div
                {...getRootProps()}
                data-testid="upload-drop-zone"
                className={cn(
                    "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors",
                    isDragActive ? "border-primary bg-primary/5" : "border-[#27272a] hover:border-primary/50 hover:bg-[#18181b]",
                    uploading && "opacity-50 pointer-events-none"
                )}
            >
                <input {...getInputProps()} data-testid="file-upload-input" />
                <Upload className="mx-auto h-12 w-12 text-[#71717a] mb-4" />
                <p className="text-sm text-white font-medium">Drag & drop files here, or click to select</p>
                <p className="text-xs text-[#71717a] mt-2">Supports any file type. Large files will be chunked.</p>
            </div>

            {files.length > 0 && (
                <div className="space-y-2">
                    {files.map(file => (
                        <div key={file.id} className="bg-[#18181b] rounded-md p-3 flex items-center gap-3 border border-[#27272a]">
                            <File className="h-5 w-5 text-primary shrink-0" />
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center justify-between mb-1">
                                    <p className="text-xs font-medium text-white truncate">{file.file.name}</p>
                                    <span className={cn(
                                        "text-[10px] uppercase font-bold",
                                        file.status === 'completed' ? "text-green-500" :
                                            file.status === 'error' ? "text-red-500" :
                                                file.status === 'uploading' ? "text-primary" : "text-[#71717a]"
                                    )}>
                                        {file.status === 'error' ? 'Failed' : file.status === 'completed' ? 'Done' : `${file.progress}%`}
                                    </span>
                                </div>
                                <div className="h-1 w-full bg-[#27272a] rounded-full overflow-hidden">
                                    <div
                                        className={cn(
                                            "h-full transition-all duration-300",
                                            file.status === 'completed' ? "bg-green-500" :
                                                file.status === 'error' ? "bg-red-500" :
                                                    "bg-primary"
                                        )}
                                        style={{ width: `${file.progress}%` }}
                                    />
                                </div>
                            </div>
                            {!uploading && file.status !== 'completed' && (
                                <button
                                    onClick={(e) => { e.stopPropagation(); removeFile(file.id); }}
                                    className="p-1 hover:bg-[#27272a] rounded-md text-[#71717a] hover:text-white"
                                >
                                    <X className="h-4 w-4" />
                                </button>
                            )}
                            {file.status === 'completed' && <CheckCircle className="h-4 w-4 text-green-500" />}
                            {file.status === 'error' && <AlertCircle className="h-4 w-4 text-red-500" />}
                        </div>
                    ))}
                </div>
            )}

            <div className="flex justify-end pt-4">
                <button
                    onClick={() => startUploads()}
                    disabled={uploading || files.length === 0 || files.every(f => f.status === 'completed')}
                    data-testid="upload-button"
                    className="flex items-center gap-2 bg-primary text-black px-4 py-2 rounded-md text-xs font-bold uppercase disabled:opacity-50 disabled:cursor-not-allowed hover:bg-primary/90 transition-colors"
                >
                    {uploading && <RefreshCw className="h-4 w-4 animate-spin" />}
                    {uploading ? 'Uploading...' : 'Upload Files'}
                </button>

                {files.some(f => f.status === 'error') && !uploading && (
                    <button
                        onClick={handleRetryFailed}
                        className="flex items-center gap-2 bg-red-500/10 border border-red-500/50 text-red-500 hover:bg-red-500/20 px-4 py-2 rounded-md text-xs font-bold uppercase transition-colors"
                    >
                        <RefreshCw className="h-4 w-4" />
                        Retry Failed
                    </button>
                )}
            </div>
        </div>
    );
}

export default BrowserUploadStep;
