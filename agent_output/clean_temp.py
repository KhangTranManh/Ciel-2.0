import os
import shutil
import tempfile
import platform
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_temp_dirs():
    """Return list of temporary directories to clean based on OS."""
    dirs = []
    # System temp directory
    dirs.append(tempfile.gettempdir())
    
    # User temp directories
    if platform.system() == 'Windows':
        dirs.append(os.environ.get('TEMP', ''))
        dirs.append(os.environ.get('TMP', ''))
        # Windows prefetch (optional, safe to clean)
        prefetch = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Prefetch')
        if os.path.exists(prefetch):
            dirs.append(prefetch)
    elif platform.system() == 'Linux':
        dirs.extend(['/tmp', '/var/tmp'])
        # User cache directories
        home = Path.home()
        dirs.append(str(home / '.cache'))
    elif platform.system() == 'Darwin':  # macOS
        dirs.extend(['/tmp', '/var/tmp'])
        home = Path.home()
        dirs.append(str(home / 'Library' / 'Caches' / 'com.apple.helpd'))
        dirs.append(str(home / 'Library' / 'Caches' / 'com.apple.appstore'))
    
    # Remove empty or non-existent paths
    return [d for d in dirs if d and os.path.exists(d)]

def clean_temp_files(dry_run=False):
    """
    Safely clean temporary files and free up disk space.
    
    Args:
        dry_run: If True, only log what would be deleted without actually deleting.
    
    Returns:
        dict: Statistics of cleaned items.
    """
    stats = {'files_deleted': 0, 'dirs_deleted': 0, 'errors': 0, 'bytes_freed': 0}
    temp_dirs = get_temp_dirs()
    
    for temp_dir in set(temp_dirs):  # Remove duplicates
        if not os.path.isdir(temp_dir):
            continue
        
        logging.info(f"Cleaning: {temp_dir}")
        
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            # Skip the root directory itself
            if root == temp_dir:
                continue
            
            # Delete files
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    if not dry_run:
                        file_size = os.path.getsize(file_path)
                        os.remove(file_path)
                        stats['bytes_freed'] += file_size
                    stats['files_deleted'] += 1
                except (PermissionError, OSError) as e:
                    stats['errors'] += 1
                    logging.debug(f"Could not delete {file_path}: {e}")
            
            # Delete empty directories
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    if not dry_run:
                        os.rmdir(dir_path)
                    stats['dirs_deleted'] += 1
                except (PermissionError, OSError) as e:
                    stats['errors'] += 1
                    logging.debug(f"Could not remove directory {dir_path}: {e}")
    
    # Clean specific known temp locations that os.walk might miss
    for temp_dir in set(temp_dirs):
        if not os.path.isdir(temp_dir):
            continue
        try:
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        if not dry_run:
                            file_size = os.path.getsize(item_path)
                            os.remove(item_path)
                            stats['bytes_freed'] += file_size
                        stats['files_deleted'] += 1
                    elif os.path.isdir(item_path):
                        if not dry_run:
                            shutil.rmtree(item_path, ignore_errors=True)
                        stats['dirs_deleted'] += 1
                except (PermissionError, OSError) as e:
                    stats['errors'] += 1
                    logging.debug(f"Could not delete {item_path}: {e}")
        except PermissionError:
            stats['errors'] += 1
            logging.debug(f"Permission denied listing {temp_dir}")
    
    return stats

def main():
    """Main entry point for the script."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Safely clean temporary files and free up disk space.')
    parser.add_argument('--dry-run', action='store_true', help='Simulate cleaning without deleting files')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    logging.info("Starting temporary file cleanup...")
    stats = clean_temp_files(dry_run=args.dry_run)
    
    logging.info(f"Cleanup completed:")
    logging.info(f"  Files deleted: {stats['files_deleted']}")
    logging.info(f"  Directories removed: {stats['dirs_deleted']}")
    logging.info(f"  Errors encountered: {stats['errors']}")
    logging.info(f"  Space freed: {stats['bytes_freed'] / (1024*1024):.2f} MB")

if __name__ == "__main__":
    main()