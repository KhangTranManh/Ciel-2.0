import os
import glob
import logging
from pathlib import Path

def cleanup_temp_files(temp_dir: str, extensions: tuple = ('.tmp', '.log')):
    """
    Safely remove files with specified extensions from a temporary directory.
    
    Args:
        temp_dir: Path to the temporary directory
        extensions: Tuple of file extensions to remove (default: .tmp and .log)
    """
    logger = logging.getLogger(__name__)
    
    # Validate the directory exists and is a directory
    temp_path = Path(temp_dir).resolve()
    if not temp_path.exists():
        logger.warning(f"Directory does not exist: {temp_dir}")
        return
    if not temp_path.is_dir():
        logger.error(f"Path is not a directory: {temp_dir}")
        return
    
    # Safety check: ensure we're not targeting system directories
    system_dirs = {'/', '/bin', '/sbin', '/etc', '/usr', '/var', '/sys', '/proc', '/dev'}
    if str(temp_path) in system_dirs or any(str(temp_path).startswith(d) for d in system_dirs):
        logger.error(f"Refusing to clean system directory: {temp_dir}")
        return
    
    # Process each extension
    for ext in extensions:
        pattern = f"*{ext}"
        for file_path in temp_path.glob(pattern):
            if file_path.is_file():
                try:
                    file_path.unlink()
                    logger.info(f"Removed: {file_path}")
                except PermissionError:
                    logger.warning(f"Permission denied: {file_path}")
                except OSError as e:
                    logger.warning(f"Failed to remove {file_path}: {e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    cleanup_temp_files("/tmp/my_temp_dir")