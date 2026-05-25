import psutil
import datetime
import sys
import os

def main():
    """
    Captures system diagnostics (CPU, memory, disk usage) and appends them
    to a specified file along with a timestamp.
    """
    if len(sys.argv) != 2:
        print("Usage: python system_diagnostics.py <output_file_path>")
        sys.exit(1)

    output_file_path = sys.argv[1]

    try:
        # Get current timestamp
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Get CPU usage percentage (non-blocking, interval=None for instant)
        # Using interval=1 for a more accurate reading over 1 second
        cpu_percent = psutil.cpu_percent(interval=1)

        # Get memory usage percentage
        memory_info = psutil.virtual_memory()
        memory_percent = memory_info.percent

        # Get disk usage percentage for C: drive
        # Note: This assumes a Windows system with a C: drive.
        # For Linux/macOS, you might use '/' or a specific mount point.
        if sys.platform.startswith('win'):
            disk_path = 'C:\\'
        else:
            disk_path = '/' # Default for Unix-like systems

        disk_info = psutil.disk_usage(disk_path)
        disk_percent = disk_info.percent

        # Format the metrics into a single string
        metrics_string = (
            f"{timestamp} - "
            f"CPU: {cpu_percent:.2f}% | "
            f"Memory: {memory_percent:.2f}% | "
            f"Disk ({disk_path}): {disk_percent:.2f}%"
        )

        # Append the string to the specified file
        with open(output_file_path, 'a') as f:
            f.write(metrics_string + '\n')

        # print(f"Diagnostics successfully appended to {output_file_path}")

    except FileNotFoundError:
        print(f"Error: The specified file path '{output_file_path}' was not found.")
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied to write to '{output_file_path}'.")
        sys.exit(1)
    except psutil.NoSuchProcess:
        print("Error: A process was not found by psutil (unlikely for system metrics).")
        sys.exit(1)
    except psutil.AccessDenied:
        print("Error: Access denied by psutil. Run script with appropriate permissions.")
        sys.exit(1)
    except psutil.Error as e:
        print(f"Error: An psutil-related error occurred: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()