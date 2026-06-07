import psutil
import datetime

def get_system_uptime():
    """
    Calculates and prints the system uptime in a human-readable format.
    """
    boot_time_timestamp = psutil.boot_time()
    boot_datetime = datetime.datetime.fromtimestamp(boot_time_timestamp)
    current_datetime = datetime.datetime.now()

    uptime_delta = current_datetime - boot_datetime

    total_seconds = int(uptime_delta.total_seconds())

    days = total_seconds // (24 * 3600)
    total_seconds %= (24 * 3600)
    hours = total_seconds // 3600
    total_seconds %= 3600
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    print(f"System Uptime: {days} days, {hours} hours, {minutes} minutes, {seconds} seconds")

if __name__ == "__main__":
    get_system_uptime()