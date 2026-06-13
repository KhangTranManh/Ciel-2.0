import datetime
import os

def write_checkpoint():
    """
    Writes 'Daily checkpoint' followed by the current timestamp to
    'ciel_workspace/checkpoint.txt'.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    checkpoint_string = f"Daily checkpoint {timestamp}\n"
    
    # Ensure the directory exists
    output_dir = "ciel_workspace"
    os.makedirs(output_dir, exist_ok=True)
    
    output_file_path = os.path.join(output_dir, "checkpoint.txt")
    
    try:
        with open(output_file_path, 'w') as f:
            f.write(checkpoint_string)
    except IOError as e:
        # Handle potential file writing errors
        print(f"Error writing to file {output_file_path}: {e}")

if __name__ == "__main__":
    write_checkpoint()