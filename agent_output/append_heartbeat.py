import datetime
import os

def append_heartbeat_log(log_file_path: str):
    """Appends a timestamped 'fallback heartbeat' line to the specified log file."""
    try:
        # Get current timestamp and format it
        current_time = datetime.datetime.now()
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp_str} fallback heartbeat\n"

        # Ensure the directory for the log file exists
        log_dir = os.path.dirname(log_file_path)
        if log_dir:  # Only try to create if a directory path is present
            os.makedirs(log_dir, exist_ok=True)

        # Open the file in append mode and write the log line
        with open(log_file_path, 'a') as f:
            f.write(log_line)
    except IOError as e:
        # Handle file I/O errors (e.g., permissions, disk full)
        print(f"Error writing to log file '{log_file_path}': {e}")
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")

# Define the path to the heartbeat log file
LOG_FILE_PATH = 'ciel_workspace/heartbeat.log'

# Call the function to append the heartbeat log
append_heartbeat_log(LOG_FILE_PATH)