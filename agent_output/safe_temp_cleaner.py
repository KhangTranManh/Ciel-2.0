import os
import tempfile
import shutil
import stat
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def clear_temp_files():
    temp_dir = tempfile.gettempdir()
    if not os.path.isdir(temp_dir):
        logging.error(f"Temporary directory {temp_dir} does not exist.")
        return

    for root, dirs, files in os.walk(temp_dir, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                # Skip files that are locked or system-critical by attempting to open with exclusive access
                with open(file_path, 'r+b') as f:
                    try:
                        f.seek(0, os.SEEK_END)
                    except OSError:
                        continue
                os.remove(file_path)
                logging.info(f"Deleted file: {file_path}")
            except (PermissionError, OSError, FileNotFoundError):
                logging.warning(f"Skipped locked or inaccessible file: {file_path}")
                continue

        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                # Check if directory is empty before removal
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    logging.info(f"Deleted empty directory: {dir_path}")
                else:
                    logging.debug(f"Skipped non-empty directory: {dir_path}")
            except (PermissionError, OSError, FileNotFoundError):
                logging.warning(f"Skipped locked or inaccessible directory: {dir_path}")
                continue

if __name__ == "__main__":
    clear_temp_files()