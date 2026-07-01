import os
import shutil
import tempfile
import platform
import stat
import errno

def handle_remove_readonly(func, path, exc_info):
    """Clear read-only bit and retry deletion."""
    exc = exc_info[1]
    if isinstance(exc, PermissionError) and exc.errno == errno.EACCES:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    else:
        raise

def cleanup_temp_files():
    """Remove files and directories from the system's temporary directory."""
    temp_dir = tempfile.gettempdir()
    if not os.path.isdir(temp_dir):
        print(f"Temporary directory not found: {temp_dir}")
        return

    removed_count = 0
    error_count = 0

    for root, dirs, files in os.walk(temp_dir, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                os.remove(file_path)
                removed_count += 1
            except Exception:
                error_count += 1

        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                shutil.rmtree(dir_path, onerror=handle_remove_readonly)
                removed_count += 1
            except Exception:
                error_count += 1

    print(f"Cleanup complete. Removed {removed_count} items. Errors: {error_count}")

if __name__ == "__main__":
    cleanup_temp_files()