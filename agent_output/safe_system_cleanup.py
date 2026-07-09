import os
import sys
import shutil
import tempfile
import logging
import ctypes
import platform
import winreg
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('system_cleanup.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def is_admin():
    """Check if script has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def get_system_drive():
    """Get the system drive letter (e.g., 'C:')."""
    return os.environ.get('SystemDrive', 'C:')

def get_fixed_drives():
    """Get list of fixed (non-removable) drives."""
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in range(26):
        if bitmask & (1 << letter):
            drive = f"{chr(65 + letter)}:\\"
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
            # DRIVE_FIXED = 3
            if drive_type == 3:
                drives.append(drive)
    return drives

def is_safe_drive(path):
    """
    Strict safety check: refuse to operate on system drive or any fixed drive.
    Returns True only if the path is on a removable drive.
    """
    path = Path(path)
    drive = path.drive
    if not drive:
        return False
    
    system_drive = get_system_drive()
    fixed_drives = get_fixed_drives()
    
    if drive.upper() == system_drive.upper():
        logger.error(f"SAFETY BLOCKED: Path '{path}' is on system drive {system_drive}")
        return False
    
    if drive.upper() in [d.upper() for d in fixed_drives]:
        logger.error(f"SAFETY BLOCKED: Path '{path}' is on fixed drive {drive}")
        return False
    
    return True

def safe_cleanup_directory(dir_path):
    """Safely clean up a directory with all safety checks."""
    if not os.path.exists(dir_path):
        logger.warning(f"Directory does not exist: {dir_path}")
        return
    
    if not is_safe_drive(dir_path):
        logger.error(f"Refusing to clean unsafe path: {dir_path}")
        return
    
    try:
        total_size = 0
        total_files = 0
        
        for root, dirs, files in os.walk(dir_path):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    size = os.path.getsize(file_path)
                    os.remove(file_path)
                    total_size += size
                    total_files += 1
                    logger.debug(f"Deleted file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")
            
            for dir in dirs:
                dir_full = os.path.join(root, dir)
                try:
                    shutil.rmtree(dir_full)
                    logger.debug(f"Deleted directory: {dir_full}")
                except Exception as e:
                    logger.warning(f"Failed to delete directory {dir_full}: {e}")
        
        logger.info(f"Cleaned {total_files} files, {total_size / (1024*1024):.2f} MB from {dir_path}")
    except Exception as e:
        logger.error(f"Error cleaning {dir_path}: {e}")

def clean_temp_files():
    """Clean system temp directories."""
    temp_dirs = [
        tempfile.gettempdir(),
        os.environ.get('TMP', ''),
        os.environ.get('TEMP', ''),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp'),
    ]
    
    for temp_dir in set(filter(None, temp_dirs)):
        if os.path.exists(temp_dir):
            safe_cleanup_directory(temp_dir)

def clean_user_cache():
    """Clean common user cache directories."""
    user_profile = os.environ.get('USERPROFILE', '')
    cache_dirs = [
        os.path.join(user_profile, 'AppData', 'Local', 'Microsoft', 'Windows', 'INetCache'),
        os.path.join(user_profile, 'AppData', 'Local', 'Microsoft', 'Windows', 'Temporary Internet Files'),
        os.path.join(user_profile, 'AppData', 'Local', 'Temp'),
        os.path.join(user_profile, 'AppData', 'Roaming', 'Microsoft', 'Windows', 'Cookies'),
        os.path.join(user_profile, 'AppData', 'Local', 'Microsoft', 'Windows', 'Caches'),
    ]
    
    for cache_dir in cache_dirs:
        if os.path.exists(cache_dir):
            safe_cleanup_directory(cache_dir)

def clean_prefetch():
    """Clean Windows Prefetch files (requires admin)."""
    prefetch_dir = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Prefetch')
    if os.path.exists(prefetch_dir):
        safe_cleanup_directory(prefetch_dir)

def main():
    """Main cleanup routine with safety gates."""
    logger.info("=== System Cleanup Started ===")
    
    # Safety Gate 1: Admin rights check
    if not is_admin():
        logger.error("SAFETY GATE 1: Administrator privileges required. Exiting.")
        sys.exit(1)
    logger.info("Safety Gate 1 passed: Admin rights confirmed")
    
    # Safety Gate 2: System drive identification
    system_drive = get_system_drive()
    logger.info(f"System drive identified: {system_drive}")
    
    # Safety Gate 3: Fixed drives identification
    fixed_drives = get_fixed_drives()
    logger.info(f"Fixed drives identified: {', '.join(fixed_drives)}")
    
    # Safety Gate 4: Confirm no drive operations
    logger.info("Safety Gate 4: Drive operations are strictly prohibited")
    
    # Perform cleanup operations
    logger.info("Starting temp file cleanup...")
    clean_temp_files()
    
    logger.info("Starting user cache cleanup...")
    clean_user_cache()
    
    logger.info("Starting Prefetch cleanup...")
    clean_prefetch()
    
    logger.info("=== System Cleanup Completed ===")

if __name__ == "__main__":
    main()