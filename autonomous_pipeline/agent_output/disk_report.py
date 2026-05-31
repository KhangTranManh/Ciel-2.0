import shutil

def print_disk_space_gb(path='/'):
    """
    Gets and prints the total, used, and free disk space for a given path in gigabytes (GB).
    """
    try:
        total_bytes, used_bytes, free_bytes = shutil.disk_usage(path)

        # Convert bytes to gigabytes
        total_gb = total_bytes / (1024**3)
        used_gb = used_bytes / (1024**3)
        free_gb = free_bytes / (1024**3)

        print(f"Disk Usage for '{path}':")
        print(f"  Total: {total_gb:.2f} GB")
        print(f"  Used:  {used_gb:.2f} GB")
        print(f"  Free:  {free_gb:.2f} GB")
    except FileNotFoundError:
        print(f"Error: Path '{path}' not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    print_disk_space_gb('/')