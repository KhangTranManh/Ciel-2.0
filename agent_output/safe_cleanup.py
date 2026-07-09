import os
import shutil
import tempfile
import platform
import sys

def safe_cleanup():
    """Remove temporary files safely."""
    temp_dirs = [tempfile.gettempdir()]
    if platform.system() == "Windows":
        temp_dirs.append(os.environ.get("TMP", ""))
        temp_dirs.append(os.environ.get("TEMP", ""))
    elif platform.system() == "Linux" or platform.system() == "Darwin":
        temp_dirs.extend(["/tmp", "/var/tmp"])
    
    for d in set(filter(bool, temp_dirs)):
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d, topdown=False):
            for name in files:
                try:
                    os.remove(os.path.join(root, name))
                except (PermissionError, OSError):
                    pass
            for name in dirs:
                try:
                    shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                except (PermissionError, OSError):
                    pass

def format_drive(path, dry_run=True):
    """Format a drive with safety checks. Default dry_run=True prevents accidental data loss."""
    path = os.path.abspath(path)
    system = platform.system()
    
    # Detect system drive
    if system == "Windows":
        system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\") + "\\"
        if path.rstrip("\\") + "\\" == system_drive:
            print("ERROR: Cannot format system drive (C:\\)")
            return False
    elif system == "Linux" or system == "Darwin":
        if path == "/":
            print("ERROR: Cannot format root filesystem (/)")
            return False
    
    if not os.path.exists(path):
        print(f"ERROR: Path does not exist: {path}")
        return False
    
    if dry_run:
        print(f"DRY RUN: Would format {path}")
        return True
    
    # Actual formatting (requires admin/root privileges)
    try:
        if system == "Windows":
            import subprocess
            subprocess.run(["format", path, "/Q", "/Y"], check=True)
        elif system == "Linux":
            import subprocess
            subprocess.run(["mkfs.ext4", "-F", path], check=True)
        elif system == "Darwin":
            import subprocess
            subprocess.run(["diskutil", "eraseDisk", "JHFS+", "Untitled", path], check=True)
        print(f"Formatted {path}")
        return True
    except Exception as e:
        print(f"Format failed: {e}")
        return False