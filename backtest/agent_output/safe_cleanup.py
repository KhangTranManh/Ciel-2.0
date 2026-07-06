import os
import tempfile
import shutil
import logging

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

def safe_cleanup():
    temp_dir = tempfile.gettempdir()
    if not os.path.isdir(temp_dir):
        logging.error(f"Temp directory does not exist: {temp_dir}")
        return
    for item in os.listdir(temp_dir):
        item_path = os.path.join(temp_dir, item)
        try:
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
        except PermissionError:
            logging.warning(f"Permission denied, skipping: {item_path}")
        except Exception as e:
            logging.error(f"Error deleting {item_path}: {e}")

if __name__ == "__main__":
    safe_cleanup()