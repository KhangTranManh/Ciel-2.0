import os
import tempfile
import shutil

def clean_temp_files():
    temp_dir = tempfile.gettempdir()
    deleted_files = 0
    deleted_dirs = 0
    skipped_files = 0
    skipped_dirs = 0

    for root, dirs, files in os.walk(temp_dir, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                os.remove(file_path)
                deleted_files += 1
            except (PermissionError, OSError):
                skipped_files += 1

        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                os.rmdir(dir_path)
                deleted_dirs += 1
            except (PermissionError, OSError):
                skipped_dirs += 1

    print(f"Deleted {deleted_files} files and {deleted_dirs} directories.")
    print(f"Skipped {skipped_files} files and {skipped_dirs} directories.")

if __name__ == "__main__":
    clean_temp_files()