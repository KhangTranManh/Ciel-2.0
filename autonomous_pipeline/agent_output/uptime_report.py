import psutil
import datetime

def get_system_uptime():
    """
    Retrieves and formats the system uptime.
    Handles cross-platform compatibility using the psutil library.
    """
    try:
        # Get the system boot time as a Unix timestamp
        boot_timestamp = psutil.boot_time()
        boot_time = datetime.datetime.fromtimestamp(boot_timestamp)
        current_time = datetime.datetime.now()
        uptime_delta = current_time - boot_time

        days = uptime_delta.days
        # Calculate hours, minutes, and seconds from the remaining seconds
        seconds = uptime_delta.seconds
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        uptime_parts = []
        if days > 0:
            uptime_parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            uptime_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            uptime_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        # Always include seconds if there are no other units, or if seconds are non-zero
        if seconds > 0 or not uptime_parts:
            uptime_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

        return ", ".join(uptime_parts) if uptime_parts else "Uptime information not available."

    except ImportError:
        return "Error: 'psutil' library not found. Please install it using 'pip install psutil'."
    except Exception as e:
        return f"An unexpected error occurred while getting uptime: {e}"

if __name__ == "__main__":
    uptime_info = get_system_uptime()
    print(f"System Uptime: {uptime_info}")