import datetime
import os

def log_fallback_heartbeat(log_file_path: str = 'ciel_workspace/heartbeat.log'):
    """
    Appends the current timestamp followed by 'fallback heartbeat' to the specified log file.
    Creates the directory if it doesn't exist.
    """
    try:
        # Ensure the directory exists
        log_dir = os.path.dirname(log_file_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        current_time = datetime.datetime.now()
        timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"{timestamp_str} fallback heartbeat\n"

        with open(log_file_path, 'a') as f:
            f.write(log_message)
    except IOError as e:
        # Log or print the error if the file cannot be written
        print(f"Error writing to log file '{log_file_path}': {e}")
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    log_fallback_heartbeat()