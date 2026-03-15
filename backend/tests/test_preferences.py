"""
Tests for user preferences API
"""
import pytest
from unittest.mock import Mock
from backend.database import Database


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    return database


def test_set_and_get_preference(db):
    """Test setting and getting a single preference."""
    db.set_user_preference('user1', 'theme', 'dark')
    value = db.get_user_preference('user1', 'theme')
    assert value == 'dark'


def test_get_nonexistent_preference(db):
    """Test getting a preference that doesn't exist."""
    value = db.get_user_preference('user1', 'nonexistent')
    assert value is None


def test_update_existing_preference(db):
    """Test updating an existing preference (upsert)."""
    db.set_user_preference('user1', 'theme', 'light')
    db.set_user_preference('user1', 'theme', 'dark')
    value = db.get_user_preference('user1', 'theme')
    assert value == 'dark'


def test_get_all_preferences(db):
    """Test getting all preferences for a user."""
    db.set_user_preference('user1', 'theme', 'dark')
    db.set_user_preference('user1', 'language', 'en')
    db.set_user_preference('user1', 'onboarding_completed', 'true')
    
    prefs = db.get_all_user_preferences('user1')
    assert prefs == {
        'theme': 'dark',
        'language': 'en',
        'onboarding_completed': 'true'
    }


def test_preferences_isolated_by_user(db):
    """Test that preferences are isolated between users."""
    db.set_user_preference('user1', 'theme', 'dark')
    db.set_user_preference('user2', 'theme', 'light')
    
    assert db.get_user_preference('user1', 'theme') == 'dark'
    assert db.get_user_preference('user2', 'theme') == 'light'


def test_onboarding_workflow(db):
    """Test typical onboarding preference workflow."""
    user_id = 'default'
    
    # Initially no preferences
    prefs = db.get_all_user_preferences(user_id)
    assert prefs == {}
    
    # User starts tour
    db.set_user_preference(user_id, 'onboarding_step', '0')
    assert db.get_user_preference(user_id, 'onboarding_step') == '0'
    
    # User progresses
    db.set_user_preference(user_id, 'onboarding_step', '3')
    assert db.get_user_preference(user_id, 'onboarding_step') == '3'
    
    # User completes
    db.set_user_preference(user_id, 'onboarding_completed', 'true')
    prefs = db.get_all_user_preferences(user_id)
    assert prefs['onboarding_completed'] == 'true'
    assert prefs['onboarding_step'] == '3'
