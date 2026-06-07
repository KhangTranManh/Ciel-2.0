import os
import shutil

def generate_disk_usage_report():
    """
    Calculates disk usage for the current working directory, converts values to GB,
    and writes a formatted report to 'autonomous_pipeline/agent_output/report.txt'.
    """
    output_dir = "autonomous_pipeline/agent_output"
    output_file_path = os.path.join(output_dir, "report.txt")

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    current_directory = os.getcwd()
    
    try:
        total_bytes, used_bytes, free_bytes = shutil.disk_usage(current_directory)

        # Convert bytes to gigabytes
        GB = 1024**3
        total_gb = total_bytes / GB
        used_gb = used_bytes / GB
        free_gb = free_bytes / GB

        report_content = (
            f"Disk Usage Report for: {current_directory}\n"
            f"-----------------------------------------\n"
            f"Total Disk Space: {total_gb:.2f} GB\n"
            f"Used Disk Space:  {used_gb:.2f} GB\n"
            f"Free Disk Space:  {free_gb:.2f} GB\n"
        )

        with open(output_file_path, "w") as f:
            f.write(report_content)
            
    except Exception as e:
        # Handle potential errors during disk usage calculation or file writing
        error_report_content = f"Error generating disk usage report: {e}\n"
        with open(output_file_path, "w") as f:
            f.write(error_report_content)

if __name__ == "__main__":
    generate_disk_usage_report()