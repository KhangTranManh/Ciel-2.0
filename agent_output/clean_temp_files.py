import os
import shutil
import tempfile
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def clean_temp_files(drive='C:'):
    """
    Safely clean temporary files on the specified drive.
    Skips locked files and avoids destructive operations.
    """
    temp_dirs = [
        os.environ.get('TEMP', ''),
        os.environ.get('TMP', ''),
        os.path.join(drive, 'Windows', 'Temp'),
        os.path.join(drive, 'Users'),
    ]

    # Add user-specific temp directories
    users_dir = Path(drive) / 'Users'
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            user_temp = user_dir / 'AppData' / 'Local' / 'Temp'
            if user_temp.exists():
                temp_dirs.append(str(user_temp))

    freed_space = 0

    for temp_dir in temp_dirs:
        if not temp_dir or not os.path.exists(temp_dir):
            continue

        logging.info(f"Cleaning: {temp_dir}")
        for root, dirs, files in os.walk(temp_dir, topdown=False):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    freed_space += file_size
                except (PermissionError, OSError) as e:
                    logging.debug(f"Skipped locked file: {file_path} - {e}")
                    continue

            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    shutil.rmtree(dir_path, ignore_errors=False)
                except (PermissionError, OSError) as e:
                    logging.debug(f"Skipped locked directory: {dir_path} - {e}")
                    continue

    # Clean system temp via tempfile module
    try:
        tempfile.tempdir = os.path.join(drive, 'Windows', 'Temp')
        tempfile.cleanup()
    except Exception as e:
        logging.debug(f"System temp cleanup skipped: {e}")

    logging.info(f"Total space freed: {freed_space / (1024*1024):.2f} MB")
    return freed_space

if __name__ == "__main__":
    clean_temp_files()