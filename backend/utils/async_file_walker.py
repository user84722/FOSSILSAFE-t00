"""
Async file walking utilities for efficient directory traversal.
Provides non-blocking file listing with configurable concurrency.
"""
import asyncio
import os
import logging
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class FileInfo:
    """Information about a file."""
    path: str
    size: int
    mtime: float
    is_dir: bool

class AsyncFileWalker:
    """
    Async directory walker with parallel traversal support.
    """
    
    def __init__(self, max_concurrent: int = 10, chunk_size: int = 1000):
        """
        Initialize async file walker.
        
        Args:
            max_concurrent: Maximum number of concurrent directory scans
            chunk_size: Number of files to yield per chunk
        """
        self.max_concurrent = max_concurrent
        self.chunk_size = chunk_size
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cancelled = False
    
    def cancel(self):
        """Cancel the current walk operation."""
        self._cancelled = True
    
    async def walk(self, root_path: str) -> AsyncIterator[List[FileInfo]]:
        """
        Recursively walk a directory tree asynchronously.
        
        Yields chunks of FileInfo objects to avoid memory issues.
        
        Args:
            root_path: Root directory to walk
            
        Yields:
            Lists of FileInfo objects (chunked)
        """
        self._cancelled = False
        dir_queue = asyncio.Queue()
        result_queue = asyncio.Queue()
        
        # Start with root directory
        await dir_queue.put(root_path)
        active_tasks = 0
        results = []
        
        async def process_directory(dir_path: str):
            """Process a single directory and add results to queue."""
            async with self._semaphore:
                if self._cancelled:
                    return
                
                try:
                    # Run blocking os.scandir in thread pool
                    entries = await asyncio.to_thread(self._scan_directory, dir_path)
                    
                    for entry in entries:
                        if self._cancelled:
                            return
                        
                        file_info = FileInfo(
                            path=entry['path'],
                            size=entry['size'],
                            mtime=entry['mtime'],
                            is_dir=entry['is_dir']
                        )
                        
                        # Add to result queue
                        await result_queue.put(file_info)
                        
                        # If it's a directory, add to dir queue for processing
                        if entry['is_dir']:
                            await dir_queue.put(entry['path'])
                
                except PermissionError as e:
                    logger.warning(f"Permission denied: {dir_path}: {e}")
                except Exception as e:
                    logger.error(f"Error scanning directory {dir_path}: {e}")
        
        # Start processing task
        async def worker():
            """Worker that processes directories from queue."""
            nonlocal active_tasks
            while True:
                try:
                    dir_path = await asyncio.wait_for(dir_queue.get(), timeout=0.1)
                    active_tasks += 1
                    await process_directory(dir_path)
                    active_tasks -= 1
                    dir_queue.task_done()
                except asyncio.TimeoutError:
                    if dir_queue.empty() and active_tasks == 0:
                        break
                except Exception as e:
                    logger.error(f"Worker error: {e}")
                    active_tasks -= 1
        
        # Start workers
        workers = [asyncio.create_task(worker()) for _ in range(self.max_concurrent)]
        
        # Collect results and yield in chunks
        try:
            while True:
                # Check if we're done
                if all(w.done() for w in workers) and result_queue.empty():
                    break
                
                if self._cancelled:
                    break
                
                try:
                    # Get result with timeout
                    file_info = await asyncio.wait_for(result_queue.get(), timeout=0.1)
                    results.append(file_info)
                    
                    # Yield chunk if we've accumulated enough
                    if len(results) >= self.chunk_size:
                        yield results.copy()
                        results.clear()
                
                except asyncio.TimeoutError:
                    # No results available, continue
                    continue
        
        finally:
            # Cancel workers if needed
            if self._cancelled:
                for worker in workers:
                    worker.cancel()
            
            # Wait for workers to finish
            await asyncio.gather(*workers, return_exceptions=True)
        
        # Yield any remaining results
        if results:
            yield results

    
    def _scan_directory(self, dir_path: str) -> List[Dict]:
        """
        Synchronously scan a directory (to be run in thread pool).
        
        Args:
            dir_path: Directory to scan
            
        Returns:
            List of file/directory info dictionaries
        """
        entries = []
        
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        entries.append({
                            'path': entry.path,
                            'size': stat.st_size if not entry.is_dir() else 0,
                            'mtime': stat.st_mtime,
                            'is_dir': entry.is_dir(follow_symlinks=False)
                        })
                    except (OSError, PermissionError) as e:
                        logger.warning(f"Cannot stat {entry.path}: {e}")
                        continue
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"Error in scandir for {dir_path}: {e}")
            raise
        
        return entries


async def async_walk_directory(
    root_path: str,
    max_concurrent: int = 10,
    chunk_size: int = 1000
) -> AsyncIterator[List[FileInfo]]:
    """
    Convenience function for async directory walking.
    
    Args:
        root_path: Root directory to walk
        max_concurrent: Maximum concurrent directory scans
        chunk_size: Files per chunk
        
    Yields:
        Lists of FileInfo objects
    """
    walker = AsyncFileWalker(max_concurrent, chunk_size)
    async for chunk in walker.walk(root_path):
        yield chunk
