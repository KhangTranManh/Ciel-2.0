import os
import glob
import datetime

# Define target date for the report
TARGET_DATE_STR = "2026-05-26"

# Define paths for desktop and archive directories
# os.path.expanduser('~') resolves to the user's home directory cross-platform
DESKTOP_PATH = os.path.join(os.path.expanduser('~'), 'Desktop')
ARCHIVE_DIR = os.path.join(os.path.expanduser('~'), 'Documents', 'Archive')

# Ensure the archive directory exists; create it if it doesn't
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# --- 1. Find the latest 'Market_Summary_*.txt' on the desktop ---
market_summary_pattern = os.path.join(DESKTOP_PATH, 'Market_Summary_*.txt')
list_of_market_summary_files = glob.glob(market_summary_pattern)

latest_market_summary_file = None
if list_of_market_summary_files:
    # Find the file with the latest modification time
    latest_market_summary_file = max(list_of_market_summary_files, key=os.path.getmtime)

# --- 2. Read the latest Market Summary file and extract BTC price movement ---
btc_price_movement = "BTC Price Movement: N/A"  # Default value if not found
current_btc_price = "Current BTC Price: N/A"    # Default value if not found

if latest_market_summary_file:
    try:
        with open(latest_market_summary_file, 'r') as f:
            for line in f:
                if "BTC Price Movement:" in line:
                    btc_price_movement = line.strip()
                elif "Current BTC Price:" in line:
                    current_btc_price = line.strip()
    except IOError:
        # Silently handle file read errors as per output rules
        pass

# --- 3. Read 'nightly_health_2026-05-26.txt' from the archive and extract system metrics ---
nightly_health_filename = f"nightly_health_{TARGET_DATE_STR}.txt"
nightly_health_filepath = os.path.join(ARCHIVE_DIR, nightly_health_filename)

cpu_usage = "CPU Usage: N/A"
memory_usage = "Memory Usage: N/A"
disk_usage = "Disk Usage: N/A"
network_latency = "Network Latency: N/A"

if os.path.exists(nightly_health_filepath):
    try:
        with open(nightly_health_filepath, 'r') as f:
            for line in f:
                if "CPU Usage:" in line:
                    cpu_usage = line.strip()
                elif "Memory Usage:" in line:
                    memory_usage = line.strip()
                elif "Disk Usage:" in line:
                    disk_usage = line.strip()
                elif "Network Latency:" in line:
                    network_latency = line.strip()
    except IOError:
        # Silently handle file read errors
        pass

# --- 4. Write extracted data into a new 'consolidated_daily_report_2026-05-26.txt' ---
consolidated_report_filename = f"consolidated_daily_report_{TARGET_DATE_STR}.txt"
consolidated_report_filepath = os.path.join(ARCHIVE_DIR, consolidated_report_filename)

try:
    with open(consolidated_report_filepath, 'w') as f:
        f.write(f"Consolidated Daily Report - {TARGET_DATE_STR}\n")
        f.write("-----------------------------------------\n\n")
        f.write("Market Summary (from latest report):\n")
        f.write(f"  {btc_price_movement}\n")
        f.write(f"  {current_btc_price}\n\n")
        f.write("System Health Metrics:\n")
        f.write(f"  {cpu_usage}\n")
        f.write(f"  {memory_usage}\n")
        f.write(f"  {disk_usage}\n")
        f.write(f"  {network_latency}\n")
except IOError:
    # Silently handle file write errors
    pass