import os
import shutil
import psutil
import subprocess
import sys
import tempfile
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
MIN_DISK_SPACE_GB = 5
CRITICAL_PROCESSES = ['apt', 'dpkg', 'yum', 'dnf', 'pacman', 'systemd-tmpfiles']
TEMP_DIRS = ['/tmp', '/var/tmp']

def check_disk_space(path: str, min_gb: float = MIN_DISK_SPACE_GB) -> bool:
    """Check if disk has at least min_gb GB free space."""
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < min_gb:
            logger.warning(f"Low disk space on {path}: {free_gb:.2f} GB free (min {min_gb} GB)")
            return False
        return True
    except FileNotFoundError:
        logger.error(f"Path {path} does not exist")
        return False
    except PermissionError:
        logger.error(f"Permission denied accessing {path}")
        return False

def check_critical_processes() -> bool:
    """Check if any critical system processes are running."""
    try:
        for proc in psutil.process_iter(['name']):
            try:
                if proc.info['name'] in CRITICAL_PROCESSES:
                    logger.warning(f"Critical process running: {proc.info['name']} (PID {proc.pid})")
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return True
    except Exception as e:
        logger.error(f"Error checking processes: {e}")
        return False

def check_system_load() -> bool:
    """Check if system load is acceptable for maintenance."""
    try:
        load1, load5, load15 = psutil.getloadavg()
        cpu_count = psutil.cpu_count()
        if load1 > cpu_count * 0.8:
            logger.warning(f"System load too high: {load1:.2f} (CPU count: {cpu_count})")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking system load: {e}")
        return False

def check_network_activity() -> bool:
    """Check if significant network activity is ongoing."""
    try:
        net_io = psutil.net_io_counters()
        if net_io.bytes_sent > 100 * 1024 * 1024 or net_io.bytes_recv > 100 * 1024 * 1024:
            logger.warning("High network activity detected")
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking network activity: {e}")
        return False

def check_all_safety_conditions() -> bool:
    """Run all safety checks and return True if all pass."""
    checks = [
        ("Disk space", check_disk_space('/')),
        ("Critical processes", check_critical_processes()),
        ("System load", check_system_load()),
        ("Network activity", check_network_activity()),
    ]
    
    all_pass = True
    for name, result in checks:
        if not result:
            logger.error(f"Safety check failed: {name}")
            all_pass = False
    
    return all_pass

def clear_temp_directories(dirs: list = None) -> bool:
    """Clear specified temporary directories safely."""
    if dirs is None:
        dirs = TEMP_DIRS
    
    success = True
    for dir_path in dirs:
        if not os.path.isdir(dir_path):
            logger.warning(f"Directory {dir_path} does not exist, skipping")
            continue
        
        try:
            for item in os.listdir(dir_path):
                item_path = os.path.join(dir_path, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except (PermissionError, OSError) as e:
                    logger.warning(f"Could not remove {item_path}: {e}")
                    continue
            logger.info(f"Cleared {dir_path}")
        except Exception as e:
            logger.error(f"Error clearing {dir_path}: {e}")
            success = False
    
    return success

def perform_maintenance():
    """Main function: check safety, then perform maintenance."""
    logger.info("Starting system maintenance safety checks")
    
    if not check_all_safety_conditions():
        logger.error("Safety checks failed. Aborting maintenance.")
        sys.exit(1)
    
    logger.info("All safety checks passed. Proceeding with maintenance.")
    
    if not clear_temp_directories():
        logger.warning("Some temporary directories could not be fully cleared")
    
    logger.info("Maintenance completed successfully")

if __name__ == "__main__":
    perform_maintenance()