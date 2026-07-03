import os
import shutil
import tempfile
import ctypes
import sys
from pathlib import Path

def is_admin():
    """Check if script runs with admin privileges (needed for some temp folders)."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def get_size(path):
    """Calculate total size of files in a directory (bytes)."""
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += get_size(entry.path)
            except (PermissionError, OSError):
                continue
    except (PermissionError, FileNotFoundError, OSError):
        pass
    return total

def clean_folder(folder_path):
    """Delete all files and subfolders in a given folder, return freed bytes."""
    if not os.path.exists(folder_path):
        return 0

    freed = 0
    for root, dirs, files in os.walk(folder_path, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                freed += os.path.getsize(file_path)
                os.remove(file_path)
            except (PermissionError, OSError):
                # Locked file — skip
                pass
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                shutil.rmtree(dir_path, ignore_errors=False)
            except (PermissionError, OSError):
                # Locked directory — skip
                pass
    return freed

def clean_temp_files():
    """Clean Windows Temp, user temp, and prefetch folders. Return total freed bytes."""
    total_freed = 0

    # User temp folder
    user_temp = tempfile.gettempdir()
    print(f"Cleaning user temp: {user_temp}")
    total_freed += clean_folder(user_temp)

    # Windows Temp folder
    win_temp = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Temp')
    if os.path.exists(win_temp):
        print(f"Cleaning Windows temp: {win_temp}")
        total_freed += clean_folder(win_temp)

    # Prefetch folder (requires admin)
    prefetch = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'Prefetch')
    if os.path.exists(prefetch):
        if is_admin():
            print(f"Cleaning Prefetch: {prefetch}")
            total_freed += clean_folder(prefetch)
        else:
            print("Skipping Prefetch — admin rights required.")

    return total_freed

if __name__ == "__main__":
    if not sys.platform.startswith('win'):
        print("This script is designed for Windows only.")
        sys.exit(1)

    print("Starting temporary file cleanup...")
    freed_bytes = clean_temp_files()
    freed_mb = freed_bytes / (1024 * 1024)
    print(f"Cleanup complete. Freed approximately {freed_mb:.2f} MB ({freed_bytes} bytes).")