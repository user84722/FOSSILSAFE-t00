import pytest
import threading
import queue
from unittest.mock import MagicMock, patch
from backend.streaming_pipeline import StreamingBackupPipeline, PipelineConfig

@pytest.fixture
def pipeline_fixture():
    db = MagicMock()
    tape_controller = MagicMock()
    smb_client = MagicMock()
    socketio = MagicMock()
    
    # 1GB soft limit
    config = PipelineConfig(
        enabled=True,
        max_queue_size_gb=1, 
        max_queue_files=10,
        staging_dir='/tmp/staging',
        producer_threads=1
    )
    
    with patch('backend.streaming_pipeline.Path') as mock_path:
        pipeline = StreamingBackupPipeline(db, tape_controller, smb_client, socketio, config)
        pipeline.queue = queue.Queue() # Reset queue
        pipeline.queue_size_bytes = 0
        return pipeline

def test_physical_limit_check(pipeline_fixture):
    """Test that queue rejects file if physical disk is full"""
    file_size = 100 * 1024 * 1024 # 100MB
    
    # Mock shutil.disk_usage to return low space (1MB free)
    with patch('shutil.disk_usage') as mock_usage:
        mock_usage.return_value.free = 1 * 1024 * 1024 # 1MB free
        
        # Should raise Exception because 1MB < 100MB + 500MB buffer AND queue is empty
        with pytest.raises(Exception, match="Staging disk is full"):
            pipeline_fixture._is_queue_full(file_size)

def test_soft_limit_enforcement(pipeline_fixture):
    """Test that queue respects soft limit when queue is not empty"""
    # Set soft limit to 1GB (approx 10^9 bytes)
    # Current queue has 500MB
    pipeline_fixture.queue.put("item")
    pipeline_fixture.queue_size_bytes = 500 * 1024 * 1024
    
    # Try adding 2GB. 0.5 + 2 = 2.5GB > 1GB.
    # Should be rejected (True)
    with patch('shutil.disk_usage') as mock_usage:
        mock_usage.return_value.free = 10 * 1024 * 1024 * 1024 # Lots of space
        assert pipeline_fixture._is_queue_full(2 * 1024 * 1024 * 1024) is True

def test_soft_limit_override(pipeline_fixture):
    """Test that queue accepts large file if queue is empty (override)"""
    # Queue is empty
    assert pipeline_fixture.queue.qsize() == 0
    assert pipeline_fixture.queue_size_bytes == 0
    
    # Try adding 5GB file (larger than 1GB limit).
    # Should be accepted (False) IF physical space allows
    with patch('shutil.disk_usage') as mock_usage:
        mock_usage.return_value.free = 10 * 1024 * 1024 * 1024 # 10GB free
        
        # Should return False (Not Full) because of override
        assert pipeline_fixture._is_queue_full(5 * 1024 * 1024 * 1024) is False
