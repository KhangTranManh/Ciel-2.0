import shutil

def get_disk_space_gb(path='/'):
    """
    Gets and prints the total, used, and free disk space for a given path
    in a human-readable format (GB).
    """
    try:
        total_b, used_b, free_b = shutil.disk_usage(path)

        # Convert bytes to gigabytes
        gb_factor = 1024**3
        total_gb = total_b / gb_factor
        used_gb = used_b / gb_factor
        free_gb = free_b / gb_factor

        print(f"Disk Usage for '{path}':")
        print(f"  Total: {total_gb:.2f} GB")
        print(f"  Used:  {used_gb:.2f} GB")
        print(f"  Free:  {free_gb:.2f} GB")

    except FileNotFoundError:
        print(f"Error: Path '{path}' not found.")
    except PermissionError:
        print(f"Error: Permission denied to access '{path}'.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    get_disk_space_gb('/')