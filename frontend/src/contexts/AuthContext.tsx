import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface User {
    id: number
    username: string
    role: string
    has_2fa: boolean
}

interface AuthContextType {
    user: User | null
    token: string | null
    isAuthenticated: boolean
    isLoading: boolean
    login: (token: string, user: User) => void
    logout: () => void
    checkAuth: () => Promise<boolean>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null>(null)
    const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
    const [isLoading, setIsLoading] = useState(true)

    const login = (newToken: string, newUser: User) => {
        localStorage.setItem('token', newToken)
        setToken(newToken)
        setUser(newUser)
    }

    const logout = async () => {
        try {
            if (token) {
                await fetch('/api/auth/logout', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` }
                })
            }
        } catch {
            // Ignore errors on logout
        }
        localStorage.removeItem('token')
        setToken(null)
        setUser(null)
    }

    const checkAuth = async (): Promise<boolean> => {
        const storedToken = localStorage.getItem('token')
        if (!storedToken) {
            setIsLoading(false)
            return false
        }

        try {
            const res = await fetch('/api/auth/me', {
                headers: { 'Authorization': `Bearer ${storedToken}` }
            })

            if (!res.ok) {
                if (res.status === 401) {
                    localStorage.removeItem('token')
                    setToken(null)
                    setUser(null)
                }
                setIsLoading(false)
                return false
            }

            const data = await res.json()
            if (data.success && data.data) {
                setUser(data.data)
                setToken(storedToken)
                setIsLoading(false)
                return true
            }
        } catch (e) {
            console.error("Auth check failed (network/parsing error):", e)
        }

        setIsLoading(false)
        return false
    }

    useEffect(() => {
        checkAuth()
    }, [])

    return (
        <AuthContext.Provider value={{
            user,
            token,
            isAuthenticated: !!user && !!token,
            isLoading,
            login,
            logout,
            checkAuth
        }}>
            {children}
        </AuthContext.Provider>
    )
}

export function useAuth() {
    const context = useContext(AuthContext)
    if (!context) {
        throw new Error('useAuth must be used within an AuthProvider')
    }
    return context
}
