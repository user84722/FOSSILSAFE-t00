import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { io, Socket } from 'socket.io-client';

interface SocketContextType {
    socket: Socket | null;
    isConnected: boolean;
}

const SocketContext = createContext<SocketContextType>({
    socket: null,
    isConnected: false,
});

export const useSocket = () => useContext(SocketContext);

interface SocketProviderProps {
    children: ReactNode;
}

export const SocketProvider: React.FC<SocketProviderProps> = ({ children }) => {
    const [socket, setSocket] = useState<Socket | null>(null);
    const [isConnected, setIsConnected] = useState(false);

    useEffect(() => {
        // The backend is served on the same host/port in production via Nginx
        // In development, Vite proxys /socket.io to the backend
        const socketInstance = io({
            path: '/socket.io/',
            transports: ['websocket'],
            autoConnect: true,
            reconnectionAttempts: 5,
        });

        socketInstance.on('connect', () => {
            console.log('Successfully connected to WebSocket');

            // Perform Auth Handshake
            const token = localStorage.getItem('token');
            if (token) {
                socketInstance.emit('auth_ping', { token }, (response: any) => {
                    if (response?.ok) {
                        console.log('WebSocket authorized successfully');
                        setIsConnected(true);
                    } else {
                        console.error('WebSocket authorization failed');
                    }
                });
            } else {
                // If no token, we might be public (dev mode)
                setIsConnected(true);
            }
        });

        socketInstance.on('disconnect', () => {
            console.log('Disconnected from WebSocket');
            setIsConnected(false);
        });

        socketInstance.on('connect_error', (error) => {
            console.error('WebSocket connection error:', error);
            setIsConnected(false);
        });

        setSocket(socketInstance);

        return () => {
            socketInstance.disconnect();
        };
    }, []);

    return (
        <SocketContext.Provider value={{ socket, isConnected }}>
            {children}
        </SocketContext.Provider>
    );
};
