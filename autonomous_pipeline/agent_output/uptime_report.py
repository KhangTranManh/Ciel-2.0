import platform
import subprocess
import ctypes
import datetime

def get_uptime_seconds():
    """
    Calculates and returns the system uptime in seconds.
    Raises RuntimeError if uptime cannot be determined for the current system.
    """
    system = platform.system()

    if system == "Linux":
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                return uptime_seconds
        except FileNotFoundError:
            raise RuntimeError("'/proc/uptime' not found. Cannot determine uptime on this Linux-like system.")
        except Exception as e:
            raise RuntimeError(f"Error reading '/proc/uptime': {e}")

    elif system == "Darwin":  # macOS
        try:
            # sysctl -n kern.boottime returns a struct timeval, then a human-readable date.
            # Example: "{ sec = 1678886400, usec = 0 } Tue Mar 14 10:40:00 2023"
            # We need to parse the 'sec' value, which is the boot timestamp.
            output = subprocess.check_output(["sysctl", "-n", "kern.boottime"]).decode().strip()
            
            # Extract the 'sec' value
            start_index = output.find("sec = ") + len("sec = ")
            end_index = output.find(",", start_index)
            if start_index == -1 or end_index == -1:
                raise ValueError("Could not parse 'sec' from kern.boottime output.")
            
            boot_timestamp = int(output[start_index:end_index])
            
            current_timestamp = datetime.datetime.now().timestamp()
            uptime_seconds = current_timestamp - boot_timestamp
            return uptime_seconds
        except (subprocess.CalledProcessError, ValueError, IndexError) as e:
            raise RuntimeError(f"Error getting uptime on macOS: {e}")

    elif system == "Windows":
        try:
            # GetTickCount64 returns the number of milliseconds since the system started.
            # It wraps around after 2^64 milliseconds (approx 584 million years), so no practical wrap-around.
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            GetTickCount64 = kernel32.GetTickCount64
            GetTickCount64.restype = ctypes.c_ulonglong # Return type is ULONGLONG
            GetTickCount64.argtypes = [] # No arguments

            milliseconds = GetTickCount64()
            uptime_seconds = milliseconds / 1000.0
            return uptime_seconds
        except Exception as e:
            raise RuntimeError(f"Error getting uptime on Windows: {e}")

    else:
        raise NotImplementedError(f"Uptime calculation not implemented for system: {system}")

def format_uptime(total_seconds):
    """
    Formats total seconds into a human-readable string (e.g., "X days, Y hours, Z minutes, W seconds").
    """
    if total_seconds < 0:
        return "System uptime is negative (error in calculation)."

    days = int(total_seconds // (24 * 3600))
    total_seconds %= (24 * 3600)
    hours = int(total_seconds // 3600)
    total_seconds %= 3600
    minutes = int(total_seconds // 60)
    seconds = int(total_seconds % 60) # Remaining seconds

    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds > 0 or not parts: # Include seconds if there are any, or if all other parts are zero
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return ", ".join(parts)

try:
    uptime_seconds = get_uptime_seconds()
    uptime_formatted = format_uptime(uptime_seconds)
    print(f"System Uptime: {uptime_formatted}")
except (RuntimeError, NotImplementedError) as e:
    print(f"Error: {e}")