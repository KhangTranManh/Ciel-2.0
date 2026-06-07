import datetime
import os

def append_fallback_heartbeat_log():
    """
    Appends the current UTC timestamp followed by 'fallback heartbeat' to
    'ciel_workspace/heartbeat.log'.
    Creates the directory if it doesn't exist.
    """
    log_dir = 'ciel_workspace'
    log_file_path = os.path.join(log_dir, 'heartbeat.log')

    try:
        # Ensure the directory exists
        os.makedirs(log_dir, exist_ok=True)

        # Get current UTC timestamp
        current_time_utc = datetime.datetime.now(datetime.timezone.utc)
        timestamp_str = current_time_utc.isoformat(timespec='seconds')

        log_message = f"{timestamp_str} fallback heartbeat\n"

        # Append to the log file
        with open(log_file_path, 'a') as f:
            f.write(log_message)

    except IOError as e:
        # Log or handle file I/O errors
        print(f"Error writing to log file {log_file_path}: {e}")
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    append_fallback_heartbeat_log()