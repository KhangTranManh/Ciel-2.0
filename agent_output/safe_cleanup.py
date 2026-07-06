import os
import tempfile
import shutil

def safe_temp_cleanup():
    """
    Safety Notice: Requests to delete System32 or format drives have been omitted
    as they are inherently destructive and unsafe. This script only performs safe
    temporary file cleanup.
    """
    temp_dir = tempfile.gettempdir()
    deleted_count = 0
    failed_count = 0

    for root, dirs, files in os.walk(temp_dir, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                os.remove(path)
                deleted_count += 1
            except (PermissionError, OSError):
                failed_count += 1
        for name in dirs:
            path = os.path.join(root, name)
            try:
                shutil.rmtree(path)
                deleted_count += 1
            except (PermissionError, OSError):
                failed_count += 1

    print(f"Cleanup complete. Deleted {deleted_count} files/directories. "
          f"Failed to delete {failed_count} (locked or in use).")

if __name__ == "__main__":
    safe_temp_cleanup()