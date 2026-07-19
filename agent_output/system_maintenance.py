import os
import psutil
import shutil
import logging
import platform
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('system_maintenance.log'),
        logging.StreamHandler()
    ]
)

# Safety thresholds
DISK_SPACE_MIN_GB = 5.0
CPU_LOAD_MAX_PERCENT = 80.0
BATTERY_MIN_PERCENT = 20.0

def check_disk_space(path='/'):
    """Check if disk has enough free space."""
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < DISK_SPACE_MIN_GB:
            logging.warning(f"Low disk space: {free_gb:.2f} GB free on {path}")
            return False
        logging.info(f"Disk space OK: {free_gb:.2f} GB free on {path}")
        return True
    except Exception as e:
        logging.error(f"Failed to check disk space: {e}")
        return False

def check_cpu_load():
    """Check if CPU load is below threshold."""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        if cpu_percent > CPU_LOAD_MAX_PERCENT:
            logging.warning(f"High CPU load: {cpu_percent}%")
            return False
        logging.info(f"CPU load OK: {cpu_percent}%")
        return True
    except Exception as e:
        logging.error(f"Failed to check CPU load: {e}")
        return False

def check_battery_status():
    """Check if battery level is sufficient (laptops only)."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            logging.info("No battery detected (desktop system)")
            return True
        if battery.percent < BATTERY_MIN_PERCENT and not battery.power_plugged:
            logging.warning(f"Low battery: {battery.percent}% and not charging")
            return False
        logging.info(f"Battery status OK: {battery.percent}%")
        return True
    except Exception as e:
        logging.error(f"Failed to check battery status: {e}")
        return False

def clear_temp_files():
    """Clear system temporary files."""
    temp_dirs = []
    if platform.system() == 'Windows':
        temp_dirs.append(os.environ.get('TEMP', ''))
        temp_dirs.append(os.environ.get('TMP', ''))
    else:
        temp_dirs.append('/tmp')
    
    for temp_dir in temp_dirs:
        if not temp_dir or not os.path.exists(temp_dir):
            continue
        try:
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
                except Exception as e:
                    logging.debug(f"Could not remove {item_path}: {e}")
            logging.info(f"Cleared temporary files in {temp_dir}")
        except Exception as e:
            logging.error(f"Failed to clear {temp_dir}: {e}")

def empty_recycle_bin():
    """Empty the system recycle bin/trash."""
    try:
        if platform.system() == 'Windows':
            subprocess.run(['cmd', '/c', 'rd /s /q C:\\$Recycle.bin'], 
                         capture_output=True, check=False)
        else:
            trash_path = Path.home() / '.local/share/Trash'
            if trash_path.exists():
                shutil.rmtree(trash_path, ignore_errors=True)
        logging.info("Recycle bin emptied")
    except Exception as e:
        logging.error(f"Failed to empty recycle bin: {e}")

def perform_maintenance():
    """Perform system maintenance if safety conditions are met."""
    logging.info("Starting system maintenance check")
    
    # Check all safety conditions
    conditions = [
        ("Disk space", check_disk_space()),
        ("CPU load", check_cpu_load()),
        ("Battery status", check_battery_status())
    ]
    
    all_safe = all(condition[1] for condition in conditions)
    
    if not all_safe:
        failed = [c[0] for c in conditions if not c[1]]
        logging.error(f"Safety conditions not met: {', '.join(failed)}. Maintenance aborted.")
        return False
    
    logging.info("All safety conditions met. Proceeding with maintenance.")
    
    # Perform maintenance operations
    clear_temp_files()
    empty_recycle_bin()
    
    logging.info("System maintenance completed successfully")
    return True

if __name__ == "__main__":
    perform_maintenance()