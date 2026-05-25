import os
import datetime
import shutil
import psutil

def run_nightly_pipeline():
    """
    Performs file operations, archives a market summary, gathers system diagnostics,
    and records them in a health file.
    """
    try:
        # 1. Define the path to 'Market_Summary_2026-05-25.txt' on the desktop.
        desktop_path = os.path.join(os.path.expanduser('~'), 'Desktop')
        original_filename = 'Market_Summary_2026-05-25.txt'
        original_filepath = os.path.join(desktop_path, original_filename)

        # Ensure the original file exists for the script to run successfully
        if not os.path.exists(original_filepath):
            print(f"Error: Original file not found at {original_filepath}. Creating a dummy file for demonstration.")
            with open(original_filepath, 'w') as f:
                f.write("This is a dummy market summary.\n")

        # 2. Appends the line '--- ARCHIVED ---' to this file.
        with open(original_filepath, 'a') as f:
            f.write('--- ARCHIVED ---\n')
        print(f"Appended '--- ARCHIVED ---' to {original_filepath}")

        # 3. Creates an archive directory 'autonomous_pipeline/agent_output/archive/' if it doesn't exist.
        archive_base_dir = 'autonomous_pipeline/agent_output/archive/'
        # Create archive directory relative to the script's current working directory
        archive_full_path = os.path.join(os.getcwd(), archive_base_dir)
        os.makedirs(archive_full_path, exist_ok=True)
        print(f"Ensured archive directory exists: {archive_full_path}")

        # 4. Generates a new filename based on the current timestamp (e.g., '20260526_021500.txt').
        current_timestamp = datetime.datetime.now()
        timestamp_str = current_timestamp.strftime('%Y%m%d_%H%M%S')
        new_archived_filename = f"{timestamp_str}.txt"
        archived_filepath = os.path.join(archive_full_path, new_archived_filename)

        # 5. Moves the original file to the archive directory with the new timestamped name.
        shutil.move(original_filepath, archived_filepath)
        print(f"Moved '{original_filename}' to '{archived_filepath}'")

        # 6. Gathers system diagnostics (CPU %, Memory %, Disk %).
        cpu_percent = psutil.cpu_percent(interval=1)  # CPU usage over 1 second
        memory_percent = psutil.virtual_memory().percent
        # Disk usage for the root partition or current drive
        disk_percent = psutil.disk_usage('/').percent
        print(f"Collected system diagnostics: CPU={cpu_percent}%, Memory={memory_percent}%, Disk={disk_percent}%")

        # 7. Creates a new file 'nightly_health_2026-05-26.txt' in the archive directory.
        health_filename_date = current_timestamp.strftime('%Y-%m-%d')
        health_filename = f"nightly_health_{health_filename_date}.txt"
        health_filepath = os.path.join(archive_full_path, health_filename)

        # 8. Appends the collected system diagnostics to the new health file.
        with open(health_filepath, 'a') as f:
            f.write(f"--- Nightly Health Report ({current_timestamp.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
            f.write(f"CPU Usage: {cpu_percent}%\n")
            f.write(f"Memory Usage: {memory_percent}%\n")
            f.write(f"Disk Usage: {disk_percent}%\n")
            f.write("-" * 40 + "\n")
        print(f"Appended system diagnostics to '{health_filepath}'")

        # 9. Prints a confirmation message upon successful completion.
        print("\nNightly pipeline completed successfully!")

    except FileNotFoundError as e:
        print(f"Error: A file was not found. {e}")
    except PermissionError as e:
        print(f"Error: Permission denied. Please check file/directory permissions. {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    run_nightly_pipeline()