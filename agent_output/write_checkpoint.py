import datetime
import os

def write_checkpoint():
    """
    Writes 'Daily checkpoint' followed by the current timestamp to
    'ciel_workspace/checkpoint.txt'. Creates the file if it doesn't exist
    or overwrites it if it does.
    """
    # Ensure the directory exists
    output_dir = 'ciel_workspace'
    os.makedirs(output_dir, exist_ok=True)

    # Get current timestamp
    current_time = datetime.datetime.now()
    timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

    # Construct the string to write
    output_string = f"Daily checkpoint {timestamp_str}\n"

    # Define the output file path
    output_file_path = os.path.join(output_dir, 'checkpoint.txt')

    # Write the string to the file, overwriting if it exists
    with open(output_file_path, 'w') as f:
        f.write(output_string)

if __name__ == "__main__":
    write_checkpoint()