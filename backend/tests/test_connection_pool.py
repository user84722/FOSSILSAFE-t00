"""
Tests for database connection pooling
"""
import pytest
import threading
import time
import tempfile
from pathlib import Path
from backend.database import Database, ConnectionPool


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path), pool_size=10, pool_timeout=10)
    yield database
    database.close()


@pytest.fixture
def pool(tmp_path):
    """Create a temporary connection pool for testing."""
    db_path = tmp_path / "test_pool.db"
    pool = ConnectionPool(str(db_path), pool_size=3, timeout=5)
    yield pool
    pool.close_all()


def test_connection_pool_creation(pool):
    """Test that connection pool is created correctly."""
    assert pool.pool_size == 3
    assert pool.timeout == 5
    assert not pool._closed


def test_get_connection(pool):
    """Test getting a connection from the pool."""
    conn = pool.get_connection()
    assert conn is not None
    
    # Verify connection works
    cursor = conn.execute('SELECT 1')
    result = cursor.fetchone()
    assert result[0] == 1


def test_thread_local_connections(pool):
    """Test that each thread gets its own connection."""
    connections = {}
    
    def get_conn(thread_id):
        conn = pool.get_connection()
        connections[thread_id] = id(conn)
    
    threads = []
    for i in range(3):
        t = threading.Thread(target=get_conn, args=(i,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    # Each thread should have gotten a different connection
    assert len(set(connections.values())) == 3


def test_connection_reuse(pool):
    """Test that connections are reused within the same thread."""
    conn1 = pool.get_connection()
    conn2 = pool.get_connection()
    
    # Same thread should get the same connection
    assert id(conn1) == id(conn2)


def test_pool_size_limit(pool):
    """Test that pool respects size limit."""
    connections = []
    
    def get_conn():
        conn = pool.get_connection()
        connections.append(id(conn))
        time.sleep(0.5)  # Hold connection briefly
    
    # Start more threads than pool size
    threads = []
    for i in range(5):
        t = threading.Thread(target=get_conn)
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    # Should have created exactly pool_size connections
    assert len(pool._all_connections) == pool.pool_size


def test_connection_timeout(pool):
    """Test that connection request times out when pool is exhausted."""
    # Fill the pool
    def hold_connection():
        conn = pool.get_connection()
        time.sleep(10)  # Hold for longer than timeout
    
    # Start threads equal to pool size
    threads = []
    for i in range(pool.pool_size):
        t = threading.Thread(target=hold_connection)
        threads.append(t)
        t.start()
    
    time.sleep(0.5)  # Let threads acquire connections
    
    # Try to get another connection (should timeout)
    def try_get_conn(result_list):
        try:
            pool.get_connection()
            result_list.append('success')
        except TimeoutError:
            result_list.append('timeout')
    
    result = []
    t = threading.Thread(target=try_get_conn, args=(result,))
    t.start()
    t.join(timeout=6)
    
    # Should have timed out
    assert 'timeout' in result
    
    # Cleanup
    for thread in threads:
        thread.join(timeout=1)


def test_connection_health_check(pool):
    """Test that dead connections are detected and replaced."""
    conn = pool.get_connection()
    
    # Simulate connection death
    conn.close()
    
    # Next get should detect dead connection and create new one
    new_conn = pool.get_connection()
    assert new_conn is not None
    
    # Verify new connection works
    cursor = new_conn.execute('SELECT 1')
    assert cursor.fetchone()[0] == 1


def test_pool_close(pool):
    """Test that pool closes all connections."""
    # Get some connections
    conn1 = pool.get_connection()
    
    # Close pool
    pool.close_all()
    
    assert pool._closed
    assert len(pool._all_connections) == 0
    
    # Should not be able to get connections after close
    with pytest.raises(RuntimeError):
        pool.get_connection()


def test_database_with_pool(db):
    """Test Database class with connection pool."""
    # Should be able to execute queries
    cursor = db.execute('SELECT 1')
    assert cursor.fetchone()[0] == 1


def test_concurrent_database_access(db):
    """Test concurrent access to database."""
    results = []
    
    def insert_and_query(thread_id):
        try:
            # Create a test table
            db.execute(f'CREATE TABLE IF NOT EXISTS test_{thread_id} (id INTEGER, value TEXT)')
            db.commit()
            
            # Insert data
            db.execute(f'INSERT INTO test_{thread_id} VALUES (?, ?)', (thread_id, f'value_{thread_id}'))
            db.commit()
            
            # Query data
            cursor = db.execute(f'SELECT value FROM test_{thread_id} WHERE id = ?', (thread_id,))
            result = cursor.fetchone()
            results.append(result[0])
        except Exception as e:
            results.append(f'error: {e}')
    
    # Run concurrent operations
    threads = []
    for i in range(5):
        t = threading.Thread(target=insert_and_query, args=(i,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    # All operations should succeed
    assert len(results) == 5
    # Check that all are successful (not errors)
    errors = [r for r in results if isinstance(r, str) and 'error:' in r]
    if errors:
        pytest.fail(f"Errors occurred: {errors}")
    assert all(isinstance(r, str) and 'value_' in r for r in results)


def test_wal_mode_enabled(db):
    """Test that WAL mode is enabled."""
    cursor = db.execute('PRAGMA journal_mode')
    mode = cursor.fetchone()[0]
    assert mode.lower() == 'wal'


def test_connection_pool_stats(pool):
    """Test connection pool statistics."""
    # Initially no connections
    assert len(pool._all_connections) == 0
    
    # Get a connection
    conn1 = pool.get_connection()
    assert len(pool._all_connections) == 1
    
    # Get another in different thread
    def get_conn():
        pool.get_connection()
    
    t = threading.Thread(target=get_conn)
    t.start()
    t.join()
    
    assert len(pool._all_connections) == 2


def test_database_close(db):
    """Test that database close works correctly."""
    # Execute a query
    db.execute('SELECT 1')
    
    # Close database
    db.close()
    
    # Pool should be closed
    assert db.pool._closed
