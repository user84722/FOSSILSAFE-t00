"""
Tests for async file walker
"""
import pytest
import asyncio
import os
import tempfile
from pathlib import Path
from backend.utils.async_file_walker import AsyncFileWalker, async_walk_directory, FileInfo


@pytest.fixture
def test_dir(tmp_path):
    """Create a test directory structure."""
    # Create directory structure:
    # test_dir/
    #   file1.txt
    #   file2.txt
    #   subdir1/
    #     file3.txt
    #     file4.txt
    #   subdir2/
    #     file5.txt
    #     subdir3/
    #       file6.txt
    
    (tmp_path / "file1.txt").write_text("content1")
    (tmp_path / "file2.txt").write_text("content2")
    
    subdir1 = tmp_path / "subdir1"
    subdir1.mkdir()
    (subdir1 / "file3.txt").write_text("content3")
    (subdir1 / "file4.txt").write_text("content4")
    
    subdir2 = tmp_path / "subdir2"
    subdir2.mkdir()
    (subdir2 / "file5.txt").write_text("content5")
    
    subdir3 = subdir2 / "subdir3"
    subdir3.mkdir()
    (subdir3 / "file6.txt").write_text("content6")
    
    return tmp_path


@pytest.mark.asyncio
async def test_async_walk_basic(test_dir):
    """Test basic async directory walking."""
    all_files = []
    
    async for chunk in async_walk_directory(str(test_dir)):
        all_files.extend(chunk)
    
    # Should find 6 files
    file_paths = [f.path for f in all_files if not f.is_dir]
    assert len(file_paths) == 6
    
    # Check that all expected files are found
    file_names = [os.path.basename(p) for p in file_paths]
    expected = ['file1.txt', 'file2.txt', 'file3.txt', 'file4.txt', 'file5.txt', 'file6.txt']
    assert sorted(file_names) == sorted(expected)


@pytest.mark.asyncio
async def test_async_walk_chunking(test_dir):
    """Test that chunking works correctly."""
    chunk_size = 2
    chunks = []
    
    async for chunk in async_walk_directory(str(test_dir), chunk_size=chunk_size):
        chunks.append(chunk)
        # Each chunk should be at most chunk_size
        assert len(chunk) <= chunk_size
    
    # Should have multiple chunks
    assert len(chunks) > 1
    
    # Total files should still be correct
    all_files = [f for chunk in chunks for f in chunk]
    file_count = len([f for f in all_files if not f.is_dir])
    assert file_count == 6


@pytest.mark.asyncio
async def test_async_walk_concurrency(test_dir):
    """Test concurrent directory scanning."""
    walker = AsyncFileWalker(max_concurrent=3)
    
    all_files = []
    async for chunk in walker.walk(str(test_dir)):
        all_files.extend(chunk)
    
    file_count = len([f for f in all_files if not f.is_dir])
    assert file_count == 6


@pytest.mark.asyncio
async def test_async_walk_cancellation(tmp_path):
    """Test that cancellation works."""
    # Create a larger directory structure to ensure multiple chunks
    for i in range(5):
        subdir = tmp_path / f"dir{i}"
        subdir.mkdir()
        for j in range(20):
            (subdir / f"file{j}.txt").write_text(f"content{i}{j}")
    
    walker = AsyncFileWalker(chunk_size=10)  # Small chunks to ensure multiple
    
    collected = []
    chunks_received = 0
    
    gen = walker.walk(str(tmp_path))
    try:
        async for chunk in gen:
            collected.extend(chunk)
            chunks_received += 1
            # Cancel after second chunk
            if chunks_received >= 2:
                walker.cancel()
                break
    finally:
        await gen.aclose()  # Properly close the generator
    
    # Should have collected at least something before cancellation
    assert len(collected) > 0
    # But not all files (100 total)
    file_count = len([f for f in collected if not f.is_dir])
    assert file_count < 100


@pytest.mark.asyncio
async def test_async_walk_empty_dir(tmp_path):
    """Test walking an empty directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    
    all_files = []
    async for chunk in async_walk_directory(str(empty_dir)):
        all_files.extend(chunk)
    
    # Should find no files (only the directory itself might be listed)
    file_count = len([f for f in all_files if not f.is_dir])
    assert file_count == 0


@pytest.mark.asyncio
async def test_async_walk_file_info(test_dir):
    """Test that FileInfo contains correct information."""
    all_files = []
    
    async for chunk in async_walk_directory(str(test_dir)):
        all_files.extend(chunk)
    
    # Check a specific file
    file1 = next((f for f in all_files if f.path.endswith('file1.txt')), None)
    assert file1 is not None
    assert file1.size > 0
    assert file1.mtime > 0
    assert not file1.is_dir


@pytest.mark.asyncio
async def test_local_source_async_list_files(test_dir):
    """Test LocalSource async_list_files integration."""
    from backend.sources.local_source import LocalSource
    
    all_files = []
    async for chunk in LocalSource.async_list_files(str(test_dir)):
        all_files.extend(chunk)
    
    # Should find 6 files
    assert len(all_files) == 6
    
    # Each file should have expected fields
    for file_info in all_files:
        assert 'path' in file_info
        assert 'size' in file_info
        assert 'mtime' in file_info
        assert 'last_modified' in file_info


@pytest.mark.asyncio
async def test_performance_comparison(tmp_path):
    """Basic performance test (not a strict benchmark)."""
    # Create a moderately sized directory structure
    for i in range(10):
        subdir = tmp_path / f"dir{i}"
        subdir.mkdir()
        for j in range(10):
            (subdir / f"file{j}.txt").write_text(f"content{i}{j}")
    
    # Time async version
    import time
    start = time.time()
    count = 0
    async for chunk in async_walk_directory(str(tmp_path)):
        count += len([f for f in chunk if not f.is_dir])
    async_time = time.time() - start
    
    assert count == 100
    # Just ensure it completes in reasonable time
    assert async_time < 5.0  # Should be much faster, but allow headroom
