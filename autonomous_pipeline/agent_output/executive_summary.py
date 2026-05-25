import os
import re

def generate_executive_summary(date_str):
    """
    Reads daily reports, extracts key information, and generates an executive summary.

    Args:
        date_str (str): The date string for the reports (e.g., '2026-05-26').
    """
    base_dir = 'autonomous_pipeline/agent_output/archive/'
    
    consolidated_report_path = os.path.join(base_dir, f'consolidated_daily_report_{date_str}.txt')
    system_health_diff_path = os.path.join(base_dir, f'system_health_diff_{date_str}.txt')
    executive_summary_path = os.path.join(base_dir, f'executive_daily_summary_{date_str}.txt')

    btc_change_percentage = None
    system_deltas = []
    
    # Ensure the archive directory exists
    os.makedirs(base_dir, exist_ok=True)

    # --- Read consolidated daily report ---
    try:
        with open(consolidated_report_path, 'r') as f:
            for line in f:
                match = re.search(r"BTC/USD 24h Change: ([+-]?\d+\.\d+)%", line)
                if match:
                    btc_change_percentage = float(match.group(1))
                    break
    except FileNotFoundError:
        print(f"Warning: Consolidated report not found at {consolidated_report_path}")
    except Exception as e:
        print(f"Error reading consolidated report: {e}")

    # --- Read system health diff report ---
    try:
        with open(system_health_diff_path, 'r') as f:
            for line in f:
                stripped_line = line.strip()
                # Exclude common diff headers and empty lines
                if stripped_line and not stripped_line.startswith(('---', '+++', '@@', 'index', 'diff')):
                    system_deltas.append(stripped_line)
    except FileNotFoundError:
        print(f"Warning: System health diff report not found at {system_health_diff_path}")
    except Exception as e:
        print(f"Error reading system health diff report: {e}")

    # --- Generate Executive Summary ---
    summary_lines = [f"Executive Daily Summary for {date_str}"]
    summary_lines.append("-" * (len(summary_lines[0])))

    # BTC/USD Change
    if btc_change_percentage is not None:
        summary_lines.append(f"BTC/USD 24h Change: {btc_change_percentage:.2f}%")
    else:
        summary_lines.append("BTC/USD 24h Change: N/A (Data not found)")

    # System Health Deltas
    if system_deltas:
        summary_lines.append("System Health Deltas:")
        for delta in system_deltas:
            summary_lines.append(f"  - {delta}")
    else:
        summary_lines.append("System Health Deltas: None")

    # Judgment
    judgment = "Stable"
    if btc_change_percentage is not None and abs(btc_change_percentage) > 2.0:  # Threshold for significant change
        judgment = "Needs attention"
    if system_deltas:
        judgment = "Needs attention"
    
    summary_lines.append(f"\nJudgment: {judgment}")

    # --- Write summary to file ---
    try:
        with open(executive_summary_path, 'w') as f:
            for line in summary_lines:
                f.write(line + '\n')
        print(f"Executive summary successfully generated at {executive_summary_path}")
    except Exception as e:
        print(f"Error writing executive summary: {e}")

if __name__ == "__main__":
    # Example usage for the specified date
    generate_executive_summary('2026-05-26')

    # --- Create dummy input files for testing ---
    # This part is for demonstration/testing purposes.
    # In a real scenario, these files would be pre-existing.
    test_date = '2026-05-26'
    base_dir = 'autonomous_pipeline/agent_output/archive/'
    os.makedirs(base_dir, exist_ok=True)

    # Dummy consolidated_daily_report_2026-05-26.txt
    with open(os.path.join(base_dir, f'consolidated_daily_report_{test_date}.txt'), 'w') as f:
        f.write("Daily Market Report\n")
        f.write("-------------------\n")
        f.write("BTC/USD 24h Change: +3.15%\n")
        f.write("ETH/USD 24h Change: -1.20%\n")
        f.write("Volume: $123B\n")

    # Dummy system_health_diff_2026-05-26.txt
    with open(os.path.join(base_dir, f'system_health_diff_{test_date}.txt'), 'w') as f:
        f.write("--- a/system_health_snapshot_2026-05-25.json\n")
        f.write("+++ b/system_health_snapshot_2026-05-26.json\n")
        f.write("@@ -1,5 +1,5 @@\n")
        f.write("-  \"CPU_Usage\": 25.5,\n")
        f.write("+  \"CPU_Usage\": 30.1,\n")
        f.write("   \"Memory_Usage\": 60.2,\n")
        f.write("+  \"Disk_IO\": \"High\",\n")
        f.write("   \"Network_Latency\": 15.3\n")
    
    print(f"\nDummy input files created for {test_date} in '{base_dir}'.")
    print("Running generate_executive_summary for the dummy files...")
    generate_executive_summary(test_date)

    # Example with stable conditions
    test_date_stable = '2026-05-27'
    with open(os.path.join(base_dir, f'consolidated_daily_report_{test_date_stable}.txt'), 'w') as f:
        f.write("Daily Market Report\n")
        f.write("BTC/USD 24h Change: -0.50%\n")
    with open(os.path.join(base_dir, f'system_health_diff_{test_date_stable}.txt'), 'w') as f:
        f.write("--- a/system_health_snapshot_2026-05-26.json\n")
        f.write("+++ b/system_health_snapshot_2026-05-27.json\n")
        f.write("@@ -1,3 +1,3 @@\n")
        f.write("   \"CPU_Usage\": 25.0,\n")
        f.write("   \"Memory_Usage\": 50.0,\n")
        f.write("   \"Network_Latency\": 10.0\n")
    print(f"\nDummy input files created for {test_date_stable} (stable) in '{base_dir}'.")
    print("Running generate_executive_summary for the stable dummy files...")
    generate_executive_summary(test_date_stable)