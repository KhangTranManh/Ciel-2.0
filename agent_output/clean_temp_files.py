import os
import shutil
import tempfile
import ctypes
import sys
import logging
from pathlib import Path

def is_admin():
    """Check if the script is running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def clean_temp_directory(path, description):
    """Delete all files and folders in a given temp directory, skipping files in use."""
    if not os.path.exists(path):
        logging.info(f"{description} directory does not exist: {path}")
        return 0, 0
    
    total_deleted = 0
    total_freed = 0
    
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                size = os.path.getsize(file_path)
                os.remove(file_path)
                total_deleted += 1
                total_freed += size
            except (PermissionError, OSError):
                # File in use or access denied - skip
                pass
        
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                shutil.rmtree(dir_path, ignore_errors=False)
                total_deleted += 1
            except (PermissionError, OSError):
                # Directory in use or access denied - skip
                pass
    
    return total_deleted, total_freed

def clean_c_drive_temp():
    """Clean standard temp directories on C: drive."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
    
    if not is_admin():
        logging.warning("Administrator privileges recommended for full cleanup.")
    
    # Define temp directories to clean
    temp_dirs = [
        (os.environ.get('TEMP', os.path.join(os.environ['USERPROFILE'], 'AppData', 'Local', 'Temp')), "User Temp"),
        (os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Temp'), "Windows Temp"),
        (os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Prefetch'), "Windows Prefetch"),
    ]
    
    total_deleted = 0
    total_freed = 0
    
    for path, description in temp_dirs:
        deleted, freed = clean_temp_directory(path, description)
        total_deleted += deleted
        total_freed += freed
        logging.info(f"Cleaned {description}: {deleted} items, {freed / (1024*1024):.2f} MB freed")
    
    # Also clean Windows SoftwareDistribution download folder (safe to delete contents)
    software_dist = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'SoftwareDistribution', 'Download')
    if os.path.exists(software_dist):
        deleted, freed = clean_temp_directory(software_dist, "Windows Update Downloads")
        total_deleted += deleted
        total_freed += freed
        logging.info(f"Cleaned Windows Update Downloads: {deleted} items, {freed / (1024*1024):.2f} MB freed")
    
    logging.info(f"Total: {total_deleted} items removed, {total_freed / (1024*1024):.2f} MB freed")

if __name__ == "__main__":
    clean_c_drive_temp()