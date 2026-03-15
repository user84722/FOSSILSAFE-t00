"""
Authentication and Role-Based Access Control (RBAC) for FossilSafe.
"""
import os
import argon2
import secrets
import functools
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from flask import request, g, jsonify
import pyotp

# Argon2 configuration for hardening
ph = argon2.PasswordHasher(
    time_cost=3,      # iterations
    memory_cost=65536, # 64MB
    parallelism=4,
    hash_len=32,
    salt_len=16
)


@dataclass
class User:
    """User model."""
    id: int
    username: str
    password_hash: str
    salt: str
    role: str  # 'admin', 'operator', or 'viewer'
    created_at: str
    is_active: bool = True
    totp_secret: Optional[str] = None
    last_login: Optional[str] = None
    sso_provider: Optional[str] = None
    sso_id: Optional[str] = None


# Granular permission system
class Permission:
    """Granular permissions for fine-grained access control."""
    
    # Tape operations
    TAPE_VIEW = "tape:view"
    TAPE_SCAN = "tape:scan"
    TAPE_INIT = "tape:init"
    TAPE_ERASE = "tape:erase"
    TAPE_MOVE = "tape:move"
    TAPE_DUPLICATE = "tape:duplicate"
    
    # Backup operations
    BACKUP_VIEW = "backup:view"
    BACKUP_CREATE = "backup:create"
    BACKUP_CANCEL = "backup:cancel"
    BACKUP_DELETE = "backup:delete"
    
    # Restore operations
    RESTORE_VIEW = "restore:view"
    RESTORE_EXECUTE = "restore:execute"
    
    # Source management
    SOURCE_VIEW = "source:view"
    SOURCE_CREATE = "source:create"
    SOURCE_MODIFY = "source:modify"
    SOURCE_DELETE = "source:delete"
    SOURCE_TEST = "source:test"
    
    # Job management
    JOB_VIEW = "job:view"
    JOB_CREATE = "job:create"
    JOB_MODIFY = "job:modify"
    JOB_CANCEL = "job:cancel"
    JOB_DELETE = "job:delete"
    
    # System settings
    SYSTEM_VIEW = "system:view"
    SYSTEM_MODIFY = "system:modify"
    SYSTEM_DIAGNOSTICS = "system:diagnostics"
    
    # User management
    USER_VIEW = "user:view"
    USER_CREATE = "user:create"
    USER_MODIFY = "user:modify"
    USER_DELETE = "user:delete"
    
    # Catalog operations
    CATALOG_VIEW = "catalog:view"
    CATALOG_SEARCH = "catalog:search"
    CATALOG_REBUILD = "catalog:rebuild"
    CATALOG_EXPORT = "catalog:export"
    CATALOG_IMPORT = "catalog:import"
    
    
    # Logs
    LOG_VIEW = "log:view"
    LOG_DOWNLOAD = "log:download"


# Permission mapping for each role
ROLE_PERMISSIONS = {
    'admin': {
        # Admins have all permissions
        Permission.TAPE_VIEW, Permission.TAPE_SCAN, Permission.TAPE_INIT, Permission.TAPE_ERASE,
        Permission.TAPE_MOVE, Permission.TAPE_DUPLICATE,
        Permission.BACKUP_VIEW, Permission.BACKUP_CREATE, Permission.BACKUP_CANCEL, Permission.BACKUP_DELETE,
        Permission.RESTORE_VIEW, Permission.RESTORE_EXECUTE,
        Permission.SOURCE_VIEW, Permission.SOURCE_CREATE, Permission.SOURCE_MODIFY, 
        Permission.SOURCE_DELETE, Permission.SOURCE_TEST,
        Permission.JOB_VIEW, Permission.JOB_CREATE, Permission.JOB_MODIFY, 
        Permission.JOB_CANCEL, Permission.JOB_DELETE,
        Permission.SYSTEM_VIEW, Permission.SYSTEM_MODIFY, Permission.SYSTEM_DIAGNOSTICS,
        Permission.USER_VIEW, Permission.USER_CREATE, Permission.USER_MODIFY, Permission.USER_DELETE,
        Permission.CATALOG_VIEW, Permission.CATALOG_SEARCH, Permission.CATALOG_REBUILD,
        Permission.CATALOG_EXPORT, Permission.CATALOG_IMPORT,
        Permission.LOG_VIEW, Permission.LOG_DOWNLOAD,
    },
    'operator': {
        # Operators can perform backups/restores but cannot modify system settings or users
        Permission.TAPE_VIEW, Permission.TAPE_SCAN, Permission.TAPE_INIT,
        Permission.TAPE_MOVE, Permission.TAPE_DUPLICATE,
        Permission.BACKUP_VIEW, Permission.BACKUP_CREATE, Permission.BACKUP_CANCEL,
        Permission.RESTORE_VIEW, Permission.RESTORE_EXECUTE,
        Permission.SOURCE_VIEW, Permission.SOURCE_CREATE, Permission.SOURCE_MODIFY, Permission.SOURCE_TEST,
        Permission.JOB_VIEW, Permission.JOB_CREATE, Permission.JOB_MODIFY, Permission.JOB_CANCEL,
        Permission.SYSTEM_VIEW, Permission.SYSTEM_DIAGNOSTICS,
        Permission.CATALOG_VIEW, Permission.CATALOG_SEARCH,
        Permission.LOG_VIEW, Permission.LOG_DOWNLOAD,
    },
    'viewer': {
        # Viewers can only read, not modify
        Permission.TAPE_VIEW,
        Permission.BACKUP_VIEW,
        Permission.RESTORE_VIEW,
        Permission.SOURCE_VIEW,
        Permission.JOB_VIEW,
        Permission.SYSTEM_VIEW,
        Permission.CATALOG_VIEW, Permission.CATALOG_SEARCH,
        Permission.LOG_VIEW,
    }
}


@dataclass
class Session:
    """Session model."""
    token: str
    user_id: int
    role: str
    has_2fa: bool
    created_at: datetime
    expires_at: datetime


class AuthManager:
    """Manages authentication and authorization."""
    
    # Session duration
    SESSION_DURATION_HOURS = 24
    
    def __init__(self, db):
        self.db = db
        self._ensure_users_table()
        self._ensure_sessions_table()

    def _ensure_sessions_table(self):
        """Create sessions table if it doesn't exist."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                has_2fa INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Migration: add has_2fa if missing
        try:
            self.db.execute("ALTER TABLE sessions ADD COLUMN has_2fa INTEGER NOT NULL DEFAULT 0")
        except:
            pass
        self.db.commit()
    
    def _ensure_users_table(self):
        """Create users table if it doesn't exist."""
        # Using a list of migrations/statements to ensure schema is up to date
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                totp_secret TEXT,
                last_login TEXT,
                sso_provider TEXT,
                sso_id TEXT
            )
        """)
        
        # Check if we need to migrate 'readonly' to 'viewer'
        self.db.execute("UPDATE users SET role = 'viewer' WHERE role = 'readonly'")
        
        # Ensure new columns exist if table was already created in earlier versions
        try:
            self.db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass # Column already exists
            
        try:
            self.db.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT")
        except Exception:
            pass # Column already exists
            
        self.db.commit()
    
    def has_permission(self, user_id: int, permission: str) -> bool:
        """Check if a user has a specific permission."""
        user = self.get_user_by_id(user_id)
        if not user:
            return False
        
        if not user.is_active:
            return False
        
        # Get permissions for user's role
        role_perms = ROLE_PERMISSIONS.get(user.role, set())
        return permission in role_perms
    
    def get_user_permissions(self, user_id: int) -> set:
        """Get all permissions for a user."""
        user = self.get_user_by_id(user_id)
        if not user or not user.is_active:
            return set()
        
        return ROLE_PERMISSIONS.get(user.role, set())
    
    def is_setup_required(self) -> bool:
        """Check if initial setup is required (no users exist)."""
        result = self.db.execute("SELECT COUNT(*) as count FROM users").fetchone()
        return result['count'] == 0

    def setup_admin(self, username: str, password: str) -> Optional[int]:
        """Creates the initial admin user during setup."""
        if not self.is_setup_required():
            return None
        return self.create_user(username, password, 'admin')
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password with Argon2id."""
        return ph.hash(password)
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return ph.verify(password_hash, password)
        except argon2.exceptions.VerifyMismatchError:
            return False
        except Exception:
            return False
    
    def create_user(self, username: str, password: str, role: str = 'viewer') -> Optional[int]:
        """
        Create a new user.
        
        Args:
            username: Unique username
            password: Plain text password (will be hashed)
            role: 'admin', 'operator', or 'viewer'
        
        Returns:
            User ID or None if failed
        """
        if role not in ('admin', 'operator', 'viewer'):
            raise ValueError("Role must be 'admin', 'operator', or 'viewer'")
        
        password_hash = self.hash_password(password)
        
        try:
            cursor = self.db.execute("""
                INSERT INTO users (username, password_hash, salt, role, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (username, password_hash, '', role, datetime.now().isoformat()))
            self.db.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"Failed to create user: {e}")
            return None
    
    def get_user(self, username: str) -> Optional[User]:
        """Get user by username."""
        result = self.db.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        
        if not result:
            return None
        
        row = dict(result)
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'],
            salt='',
            role=row['role'],
            created_at=row['created_at'],
            is_active=bool(row.get('is_active', 1)),
            totp_secret=row.get('totp_secret'),
            last_login=row.get('last_login')
        )
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        result = self.db.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        
        if not result:
            return None
        
        row = dict(result)
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'],
            salt='',
            role=row['role'],
            created_at=row['created_at'],
            is_active=bool(row.get('is_active', 1)),
            totp_secret=row.get('totp_secret'),
            last_login=row.get('last_login')
        )
    
    def list_users(self) -> list:
        """List all users (without password hashes)."""
        results = self.db.execute("SELECT id, username, role, is_active, created_at, last_login FROM users").fetchall()
        return [dict(r) for r in results]
    
    def update_user(self, user_id: int, role: str = None, is_active: bool = None) -> bool:
        """Update a user's role and/or active status."""
        updates = []
        params = []
        
        if role is not None:
            if role not in ('admin', 'operator', 'viewer'):
                return False
            updates.append("role = ?")
            params.append(role)
            
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
            
        if not updates:
            return True
            
        params.append(user_id)
        try:
            self.db.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                tuple(params)
            )
            self.db.commit()
            return True
        except Exception:
            return False

    def update_user_role(self, user_id: int, new_role: str) -> bool:
        """Update a user's role (Legacy/Convenience)."""
        return self.update_user(user_id, role=new_role)
    
    def change_password(self, user_id: int, new_password: str) -> bool:
        """Change a user's password."""
        password_hash = self.hash_password(new_password)
        
        try:
            self.db.execute(
                "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                (password_hash, '', user_id)
            )
            self.db.commit()
            return True
        except Exception:
            return False
    
    def delete_user(self, user_id: int) -> bool:
        """Delete a user."""
        try:
            self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.db.commit()
            return True
        except Exception:
            return False
            
    def get_sso_user(self, provider: str, sso_id: str) -> Optional[User]:
        """Get user by SSO provider and ID."""
        result = self.db.execute(
            "SELECT * FROM users WHERE sso_provider = ? AND sso_id = ?",
            (provider, sso_id)
        ).fetchone()
        
        if not result:
            return None
            
        row = dict(result)
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'],
            salt='',
            role=row['role'],
            created_at=row['created_at'],
            is_active=bool(row.get('is_active', 1)),
            totp_secret=row.get('totp_secret'),
            last_login=row.get('last_login'),
            sso_provider=row.get('sso_provider'),
            sso_id=row.get('sso_id')
        )

    def create_sso_user(self, username: str, provider: str, sso_id: str, role: str = 'viewer') -> Optional[int]:
        """Create a new user from SSO."""
        # Use a dummy password hash since SSO users don't have passwords
        # This prevents local login unless a password is explicitly set later
        dummy_hash = self.hash_password(secrets.token_hex(32))
        
        try:
            cursor = self.db.execute("""
                INSERT INTO users (username, password_hash, salt, role, created_at, sso_provider, sso_id, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (username, dummy_hash, '', role, datetime.now().isoformat(), provider, sso_id))
            self.db.commit()
            return cursor.lastrowid
        except Exception as e:
            print(f"Failed to create SSO user: {e}")
            return None
            
    def login_sso_user(self, user: User, has_2fa: bool = False) -> str:
        """Create session for an SSO user without password check."""
        if not user.is_active:
            self.db.log_entry('warning', 'auth', f'SSO Login failed: Account {user.username} is disabled')
            raise ValueError("Account is disabled")

        # Update last login
        self.db.commit()
        
        # Create session
        token = secrets.token_urlsafe(32)
        now = datetime.now()
        expires_at = now + timedelta(hours=self.SESSION_DURATION_HOURS)
        
        try:
            self.db.execute("""
                INSERT INTO sessions (token, user_id, role, has_2fa, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (token, user.id, user.role, 1 if has_2fa else 0, now.isoformat(), expires_at.isoformat()))
            self.db.commit()
        except Exception as e:
            self.db.log_entry('error', 'auth', f'Failed to create SSO session: {e}')
            raise ValueError("Session creation failed")
            
        return token
    
    def logout(self, token: str) -> bool:
        """Invalidate a session."""
        try:
            self.db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self.db.commit()
            return True
        except Exception:
            return False
    
    def login(self, username: str, password: str, has_2fa: bool = False) -> Optional[str]:
        """
        Authenticate user and create session.
        
        Returns:
            Session token or None if authentication failed
        """
        user = self.get_user(username)
        if not user:
            self.db.log_entry('warning', 'auth', f'Login failed: User {username} not found')
            return None
        
        if not user.is_active:
            self.db.log_entry('warning', 'auth', f'Login failed: Account {username} is disabled')
            return None
        
        if not self.verify_password(password, user.password_hash):
            self.db.log_entry('warning', 'auth', f'Login failed: Invalid password for user {username}')
            return None
        
        # Update last login
        self.db.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(), user.id)
        )
        self.db.commit()
        
        # Create session
        token = secrets.token_urlsafe(32)
        now = datetime.now()
        expires_at = now + timedelta(hours=self.SESSION_DURATION_HOURS)
        
        try:
            self.db.execute("""
                INSERT INTO sessions (token, user_id, role, has_2fa, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (token, user.id, user.role, 1 if has_2fa else 0, now.isoformat(), expires_at.isoformat()))
            self.db.commit()
        except Exception as e:
            self.db.log_entry('error', 'auth', f'Failed to create session: {e}')
            return None
        
        return token
    

    
    def validate_session(self, token: str) -> Optional[Session]:
        """Validate a session token."""
        try:
            result = self.db.execute(
                "SELECT * FROM sessions WHERE token = ?",
                (token,)
            ).fetchone()
            
            if not result:
                return None
                
            row = dict(result)
            expires_at = datetime.fromisoformat(row['expires_at'])
            
            if datetime.now() > expires_at:
                self.db.execute("DELETE FROM sessions WHERE token = ?", (token,))
                self.db.commit()
                return None
            
            return Session(
                token=row['token'],
                user_id=row['user_id'],
                role=row['role'],
                has_2fa=bool(row.get('has_2fa', 0)),
                created_at=datetime.fromisoformat(row['created_at']),
                expires_at=expires_at
            )
        except Exception:
            return None
    
    def get_current_user(self) -> Optional[User]:
        """Get current user from request context."""
        session = getattr(g, 'session', None)
        if not session:
            return None
        return self.get_user_by_id(session.user_id)

    def generate_totp_secret(self, user_id: int) -> Tuple[str, str]:
        """Generate a new TOTP secret for a user."""
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name=user.username,
            issuer_name="FossilSafe"
        )
        return secret, provisioning_uri

    def verify_2fa(self, user_id: int, code: str) -> bool:
        """Verify a TOTP code against a user's secret."""
        user = self.get_user_by_id(user_id)
        if not user or not user.totp_secret:
            return False
        
        totp = pyotp.TOTP(user.totp_secret)
        return totp.verify(code)

    def enable_2fa(self, user_id: int, secret: str, code: str) -> bool:
        """Verify code and save secret to enable 2FA."""
        totp = pyotp.TOTP(secret)
        if totp.verify(code):
            self.db.execute(
                "UPDATE users SET totp_secret = ? WHERE id = ?",
                (secret, user_id)
            )
            self.db.commit()
            return True
        return False

    def disable_2fa(self, user_id: int) -> bool:
        """Disable 2FA for a user."""
        try:
            self.db.execute(
                "UPDATE users SET totp_secret = NULL WHERE id = ?",
                (user_id,)
            )
            self.db.commit()
            return True
        except Exception:
            return False


# Global auth manager instance (initialized by app)
_auth_manager: Optional[AuthManager] = None


def init_auth(db) -> AuthManager:
    """Initialize the auth manager."""
    global _auth_manager
    _auth_manager = AuthManager(db)
    return _auth_manager


def get_auth_manager() -> Optional[AuthManager]:
    """Get the auth manager instance."""
    return _auth_manager


def require_auth(f):
    """Decorator to require authentication."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Check if session was already set by before_request (e.g., via global API key)
        if hasattr(g, 'session') and g.session:
            return f(*args, **kwargs)

        if not _auth_manager:
            # Auth not initialized, allow all
            return f(*args, **kwargs)
        
        # Check for auth token in header
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        else:
            token = request.cookies.get('session_token') or request.args.get('token') or request.args.get('session_token')
        
        if not token:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        
        session = _auth_manager.validate_session(token)
        if not session:
            return jsonify({'success': False, 'error': 'Invalid or expired session'}), 401
        
        # Security Enforcement: If user has 2FA enabled, session must have it
        user = _auth_manager.get_user_by_id(session.user_id)
        if user and user.totp_secret and not session.has_2fa:
            return jsonify({
                'success': False, 
                'error': '2FA verification required',
                'require_2fa': True
            }), 401
            
        g.session = session
        return f(*args, **kwargs)
    
    return decorated


def require_role(role: str):
    """
    Decorator to require a specific role.
    Roles have an hierarchy: admin > operator > viewer.
    Providing a role here means the user must have at least that role.
    """
    hierarchy = {'admin': 3, 'operator': 2, 'viewer': 1, 'readonly': 1}
    
    def decorator(f):
        @functools.wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            session = getattr(g, 'session', None)
            if not session:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            
            # Admin can do anything
            if session.role == 'admin':
                return f(*args, **kwargs)
            
            # Check if user has required role
            user_level = hierarchy.get(session.role, 0)
            required_level = hierarchy.get(role, 99) # 99 to fail safe if role unknown
            
            if user_level < required_level:
                return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
            
            return f(*args, **kwargs)
        
        return decorated
    return decorator


def require_admin(f):
    """Decorator to require admin role."""
    return require_role('admin')(f)




def require_permission(permission: str):
    """
    Decorator to require a specific permission.
    Provides granular access control beyond role-based checks.
    
    Usage:
        @require_permission(Permission.TAPE_ERASE)
        def erase_tape():
            ...
    """
    def decorator(f):
        @functools.wraps(f)
        @require_auth
        def decorated(*args, **kwargs):
            session = getattr(g, 'session', None)
            if not session:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            
            # Get user's permissions
            auth_manager = getattr(g, 'auth_manager', None)
            if not auth_manager:
                return jsonify({'success': False, 'error': 'Authentication system unavailable'}), 500
            
            # Check permission
            if not auth_manager.has_permission(session.user_id, permission):
                return jsonify({
                    'success': False, 
                    'error': f'Permission denied: {permission}',
                    'required_permission': permission
                }), 403
            
            return f(*args, **kwargs)
        return decorated
    return decorator

