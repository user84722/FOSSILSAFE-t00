import os
import time
from typing import Dict, List, Optional, AsyncIterator
from backend.utils.validation import validate_local_path
from backend.utils.async_file_walker import async_walk_directory, FileInfo

class LocalSource:
    """
    Handler for local filesystem sources.
    Provides methods to list files and validate access, keeping safety in mind.
    """
    
    @staticmethod
    def list_files(path: str, show_hidden: bool = False) -> Dict:
        """
        List files in a local directory.
        Returns a structure similar to what the UI expects for browsing.
        """
        # Safety check
        is_safe, error = validate_local_path(path)
        if not is_safe:
            raise PermissionError(error)

        if not os.path.exists(path):
            raise FileNotFoundError(f"Path not found: {path}")
            
        if not os.path.isdir(path):
            raise NotADirectoryError(f"Path is not a directory: {path}")
            
        if not os.access(path, os.R_OK):
            raise PermissionError(f"Path is not readable: {path}")

        entries = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith('.'):
                        continue
                        
                    try:
                        stat = entry.stat()
                        is_dir = entry.is_dir()
                        
                        entries.append({
                            'name': entry.name,
                            'path': entry.path,
                            'is_dir': is_dir,
                            'size': stat.st_size if not is_dir else 0,
                            'mtime': stat.st_mtime,
                            'last_modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
                        })
                    except OSError:
                        # Skip entries we can't stat (permissions, broke links)
                        continue
        except OSError as e:
            raise PermissionError(f"Failed to list directory: {e}")

        # Sort: Directories first, then alphabetical
        entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
        
        return {
            'path': path,
            'entries': entries,
            'parent': os.path.dirname(path) if path != '/' else None
        }

    @staticmethod
    async def async_list_files(
        path: str,
        show_hidden: bool = False,
        max_concurrent: int = 10,
        chunk_size: int = 1000
    ) -> AsyncIterator[List[Dict]]:
        """
        Asynchronously list all files in a directory tree.
        
        This is for deep scanning (recursive), not browsing.
        Yields chunks of file info dictionaries.
        
        Args:
            path: Root path to scan
            show_hidden: Include hidden files
            max_concurrent: Max concurrent directory scans
            chunk_size: Files per chunk
            
        Yields:
            Lists of file info dictionaries
        """
        # Safety check
        is_safe, error = validate_local_path(path)
        if not is_safe:
            raise PermissionError(error)

        if not os.path.exists(path):
            raise FileNotFoundError(f"Path not found: {path}")
            
        if not os.path.isdir(path):
            raise NotADirectoryError(f"Path is not a directory: {path}")
            
        if not os.access(path, os.R_OK):
            raise PermissionError(f"Path is not readable: {path}")

        # Use async walker
        async for file_chunk in async_walk_directory(path, max_concurrent, chunk_size):
            # Filter and format results
            formatted_chunk = []
            for file_info in file_chunk:
                # Skip hidden files if requested
                if not show_hidden and os.path.basename(file_info.path).startswith('.'):
                    continue
                
                # Skip directories (we only want files for backup)
                if file_info.is_dir:
                    continue
                
                formatted_chunk.append({
                    'path': file_info.path,
                    'size': file_info.size,
                    'mtime': file_info.mtime,
                    'last_modified': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(file_info.mtime))
                })
            
            if formatted_chunk:
                yield formatted_chunk

    @staticmethod
    def check_access(path: str) -> Dict:
        """
        Verify if a path is accessible and returns details.
        """
        is_safe, error = validate_local_path(path)
        if not is_safe:
            return {'ok': False, 'error': error}

        if not os.path.exists(path):
             return {'ok': False, 'error': 'Path does not exist'}
             
        if not os.access(path, os.R_OK):
             return {'ok': False, 'error': 'Path is not readable (permission denied)'}
             
        # Check access to sub-contents if it's a dir (quick check)
        details = "Readable"
        if os.path.isdir(path):
            try:
                # Try to list just one item to verify x permission (executable/traverse)
                next(os.scandir(path), None)
                details = "Directory is readable and traversable"
            except PermissionError:
                return {'ok': False, 'error': 'Directory is not traversable (permission denied)'}
        
        return {'ok': True, 'detail': details}

