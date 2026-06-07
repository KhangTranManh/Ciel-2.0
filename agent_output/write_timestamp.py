import datetime
import os

def write_checkpoint():
    """
    Writes 'Daily checkpoint' followed by the current timestamp to 'ciel_workspace/checkpoint.txt'.
    Creates the 'ciel_workspace' directory if it does not exist.
    """
    checkpoint_dir = 'ciel_workspace'
    checkpoint_file = os.path.join(checkpoint_dir, 'checkpoint.txt')

    # Ensure the directory exists
    os.makedirs(checkpoint_dir, exist_ok=True)

    current_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content_to_write = f"Daily checkpoint {current_timestamp}\n"

    try:
        with open(checkpoint_file, 'w') as f:
            f.write(content_to_write)
    except IOError as e:
        print(f"Error writing to file {checkpoint_file}: {e}")

if __name__ == "__main__":
    write_checkpoint()