import datetime
import os

LOG_FILE_PATH = 'ciel_workspace/heartbeat.log'

def append_heartbeat_log():
    """Appends a formatted heartbeat line to the log file."""
    try:
        # Get current timestamp
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"fallback heartbeat - [{current_time}]"

        # Ensure the directory exists
        log_dir = os.path.dirname(LOG_FILE_PATH)
        if log_dir: # Only create if log_dir is not empty (i.e., not just a filename in current dir)
            os.makedirs(log_dir, exist_ok=True)

        # Append the line to the log file
        with open(LOG_FILE_PATH, 'a') as f:
            f.write(log_line + '\n')

    except IOError as e:
        # Handle potential file I/O errors
        print(f"Error writing to log file '{LOG_FILE_PATH}': {e}")
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    append_heartbeat_log()