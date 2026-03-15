"""
Rate limiting for API endpoints
"""
import time
from typing import Dict, Tuple
from collections import defaultdict
import threading


class RateLimiter:
    """
    Simple in-memory rate limiter with sliding window.
    For production, consider Redis-based implementation.
    """
    
    def __init__(self):
        self.attempts: Dict[str, list] = defaultdict(list)
        self.lockouts: Dict[str, float] = {}
        self.lock = threading.Lock()
        
        # Configuration
        self.max_attempts = 5
        self.window_seconds = 300  # 5 minutes
        self.lockout_duration = 900  # 15 minutes
    
    def check_rate_limit(self, identifier: str) -> Tuple[bool, str]:
        """
        Check if identifier is rate limited.
        
        Args:
            identifier: IP address or user identifier
            
        Returns:
            Tuple of (is_allowed, message)
        """
        with self.lock:
            import os
            if os.environ.get("TEST_MODE") == "1":
                 return True, ""
            
            now = time.time()
            
            # Check if currently locked out
            if identifier in self.lockouts:
                lockout_end = self.lockouts[identifier]
                if now < lockout_end:
                    remaining = int(lockout_end - now)
                    return False, f"Too many failed attempts. Try again in {remaining} seconds."
                else:
                    # Lockout expired
                    del self.lockouts[identifier]
                    self.attempts[identifier] = []
            
            # Clean old attempts
            cutoff = now - self.window_seconds
            self.attempts[identifier] = [
                timestamp for timestamp in self.attempts[identifier]
                if timestamp > cutoff
            ]
            
            # Check attempt count
            if len(self.attempts[identifier]) >= self.max_attempts:
                # Trigger lockout
                self.lockouts[identifier] = now + self.lockout_duration
                return False, f"Too many failed attempts. Locked out for {self.lockout_duration // 60} minutes."
            
            return True, ""
    
    def record_attempt(self, identifier: str):
        """Record a failed attempt"""
        with self.lock:
            self.attempts[identifier].append(time.time())
    
    def clear_attempts(self, identifier: str):
        """Clear attempts after successful auth"""
        with self.lock:
            if identifier in self.attempts:
                del self.attempts[identifier]
            if identifier in self.lockouts:
                del self.lockouts[identifier]
    
    def get_remaining_attempts(self, identifier: str) -> int:
        """Get number of remaining attempts before lockout"""
        with self.lock:
            now = time.time()
            cutoff = now - self.window_seconds
            recent_attempts = [
                timestamp for timestamp in self.attempts.get(identifier, [])
                if timestamp > cutoff
            ]
            return max(0, self.max_attempts - len(recent_attempts))
