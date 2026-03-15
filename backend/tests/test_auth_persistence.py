import pytest
import sqlite3
import os
from datetime import datetime, timedelta
from backend.auth import AuthManager, User

class MockDB:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        
    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)
        
    def commit(self):
        self.conn.commit()
        
    def log_entry(self, level, source, message):
        print(f"[{level}] {source}: {message}")

@pytest.fixture
def auth_manager():
    db = MockDB()
    # Create tables handled by AuthManager init
    return AuthManager(db)

def test_session_persistence(auth_manager):
    # Setup user
    user_id = auth_manager.create_user("testuser", "password123", "admin")
    assert user_id is not None
    
    # Login
    token = auth_manager.login("testuser", "password123")
    assert token is not None
    
    # Verify session in DB
    row = auth_manager.db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    assert row is not None
    assert row['user_id'] == user_id
    
    # Verify validation
    session = auth_manager.validate_session(token)
    assert session is not None
    assert session.user_id == user_id
    
    # Verify logout
    assert auth_manager.logout(token) is True
    row = auth_manager.db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    assert row is None

def test_session_expiry(auth_manager):
    user_id = auth_manager.create_user("expireuser", "pass", "viewer")
    token = auth_manager.login("expireuser", "pass")
    
    # Manually expire
    past = (datetime.now() - timedelta(hours=1)).isoformat()
    auth_manager.db.execute("UPDATE sessions SET expires_at = ? WHERE token = ?", (past, token))
    
    # Validate should fail and clean up
    assert auth_manager.validate_session(token) is None
    row = auth_manager.db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    assert row is None
