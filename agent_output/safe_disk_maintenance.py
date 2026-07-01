import os
import shutil
import tempfile
import ctypes

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def safe_clean_temp_files():
    temp_dir = tempfile.gettempdir()
    if not os.path.exists(temp_dir):
        return "Temporary directory does not exist."
    
    total_before = 0
    total_freed = 0
    
    for root, dirs, files in os.walk(temp_dir):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                total_before += os.path.getsize(file_path)
                os.remove(file_path)
                total_freed += os.path.getsize(file_path)
            except (PermissionError, OSError):
                continue
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                shutil.rmtree(dir_path)
            except (PermissionError, OSError):
                continue
    
    return f"Cleaned {total_freed} bytes from {temp_dir}"

def check_disk_space(path=None):
    if path is None:
        path = tempfile.gettempdir()
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        return f"Free: {free_gb:.2f} GB / Total: {total_gb:.2f} GB"
    except:
        return "Could not check disk space."

def main():
    print("Ciel's Temp Cleaner")
    print("===================")
    print(safe_clean_temp_files())
    print(check_disk_space())

if __name__ == "__main__":
    main()