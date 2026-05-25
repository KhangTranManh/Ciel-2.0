import re
import smtplib
from email.mime.text import MIMEText

# --- Configuration ---
REPORT_FILE = 'consolidated_daily_report_2026-05-26.txt'
EMAIL_RECIPIENT = 'master@example.com'
EMAIL_SENDER = 'alert_system@example.com'
SMTP_SERVER = 'smtp.example.com'  # Replace with your SMTP server
SMTP_PORT = 587  # Replace with your SMTP port (e.g., 587 for TLS, 465 for SSL)
SMTP_USERNAME = 'alert_system@example.com' # Replace with your SMTP username
SMTP_PASSWORD = 'your_smtp_password' # Replace with your SMTP password

# --- Thresholds ---
CPU_THRESHOLD = 90.0  # Percentage
MEMORY_THRESHOLD = 90.0  # Percentage
DISK_THRESHOLD = 90.0  # Percentage
BTC_DROP_THRESHOLD = -2.0  # Percentage (e.g., -2.0 means a 2% drop)

def send_email_alert(subject, body):
    """Sends an email alert."""
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Use TLS
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"Email alert sent to {EMAIL_RECIPIENT} with subject: '{subject}'")
    except Exception as e:
        print(f"Failed to send email alert: {e}")

def main():
    report_content = ""
    try:
        with open(REPORT_FILE, 'r') as f:
            report_content = f.read()
    except FileNotFoundError:
        print(f"Error: Report file '{REPORT_FILE}' not found.")
        return
    except Exception as e:
        print(f"Error reading report file: {e}")
        return

    btc_change = None
    cpu_usage = None
    memory_usage = None
    disk_usage = None

    # Extract BTC Price Change
    btc_match = re.search(r"BTC Price Change: (-?\d+\.?\d*)%", report_content)
    if btc_match:
        btc_change = float(btc_match.group(1))

    # Extract CPU Usage
    cpu_match = re.search(r"CPU Usage: (\d+\.?\d*)%", report_content)
    if cpu_match:
        cpu_usage = float(cpu_match.group(1))

    # Extract Memory Usage
    memory_match = re.search(r"Memory Usage: (\d+\.?\d*)%", report_content)
    if memory_match:
        memory_usage = float(memory_match.group(1))

    # Extract Disk Usage
    disk_match = re.search(r"Disk Usage: (\d+\.?\d*)%", report_content)
    if disk_match:
        disk_usage = float(disk_match.group(1))

    alert_messages = []

    if btc_change is not None and btc_change < BTC_DROP_THRESHOLD:
        alert_messages.append(f"BTC Price dropped significantly: {btc_change:.2f}% (Threshold: {BTC_DROP_THRESHOLD:.2f}%)")

    if cpu_usage is not None and cpu_usage > CPU_THRESHOLD:
        alert_messages.append(f"High CPU Usage detected: {cpu_usage:.2f}% (Threshold: {CPU_THRESHOLD:.2f}%)")

    if memory_usage is not None and memory_usage > MEMORY_THRESHOLD:
        alert_messages.append(f"High Memory Usage detected: {memory_usage:.2f}% (Threshold: {MEMORY_THRESHOLD:.2f}%)")

    if disk_usage is not None and disk_usage > DISK_THRESHOLD:
        alert_messages.append(f"High Disk Usage detected: {disk_usage:.2f}% (Threshold: {DISK_THRESHOLD:.2f}%)")

    if alert_messages:
        subject = "Daily Report Alert: Threshold Breached!"
        body = "The following issues were detected in the daily report:\n\n" + "\n".join(alert_messages)
        body += f"\n\nReport Date: {REPORT_FILE.split('_')[-1].replace('.txt', '')}"
        body += f"\n\n--- Current Metrics ---\n"
        body += f"BTC Price Change: {btc_change:.2f}% (Threshold: {BTC_DROP_THRESHOLD:.2f}% drop)\n" if btc_change is not None else "BTC Price Change: N/A\n"
        body += f"CPU Usage: {cpu_usage:.2f}% (Threshold: {CPU_THRESHOLD:.2f}%)\n" if cpu_usage is not None else "CPU Usage: N/A\n"
        body += f"Memory Usage: {memory_usage:.2f}% (Threshold: {MEMORY_THRESHOLD:.2f}%)\n" if memory_usage is not None else "Memory Usage: N/A\n"
        body += f"Disk Usage: {disk_usage:.2f}% (Threshold: {DISK_THRESHOLD:.2f}%)\n" if disk_usage is not None else "Disk Usage: N/A\n"
        send_email_alert(subject, body)
    else:
        print("All clear")

if __name__ == "__main__":
    main()