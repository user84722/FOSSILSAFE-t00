import { api } from "@/lib/api"

export interface SystemStatus {
    status: string
    version: string
    // Add other fields as needed
}

export const getSystemStatus = async (): Promise<SystemStatus> => {
    const res = await api.get<SystemStatus>("/api/system/info")
    if (res.success && res.data) {
        return res.data
    }
    throw new Error(res.error || "Failed to get system status")
}
