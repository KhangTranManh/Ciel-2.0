import datetime
import os

def write_checkpoint():
    """
    Writes 'Daily checkpoint' followed by the current timestamp to
    'ciel_workspace/checkpoint.txt'.
    """
    # Ensure the directory exists
    output_dir = 'ciel_workspace'
    os.makedirs(output_dir, exist_ok=True)

    # Get current timestamp
    current_time = datetime.datetime.now()
    timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

    # Construct the full string
    checkpoint_message = f"Daily checkpoint {timestamp_str}\n"

    # Define the output file path
    file_path = os.path.join(output_dir, 'checkpoint.txt')

    # Write the string to the file
    try:
        with open(file_path, 'a') as f:  # Use 'a' for append mode
            f.write(checkpoint_message)
    except IOError as e:
        print(f"Error writing to file {file_path}: {e}")

if __name__ == "__main__":
    write_checkpoint()