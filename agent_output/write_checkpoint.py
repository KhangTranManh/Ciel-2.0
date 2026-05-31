import datetime
import os

def write_checkpoint():
    """
    Writes 'Daily checkpoint' followed by the current timestamp to 'ciel_workspace/checkpoint.txt'.
    """
    file_path = 'ciel_workspace/checkpoint.txt'
    
    # Ensure the directory for the file exists
    directory = os.path.dirname(file_path)
    if directory: # Only attempt to create if a directory path is specified
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as e:
            print(f"Error creating directory '{directory}': {e}")
            return # Exit if directory cannot be created

    current_time = datetime.datetime.now()
    # Format timestamp as YYYY-MM-DD HH:MM:SS
    timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
    
    checkpoint_entry = f"Daily checkpoint {timestamp_str}\n"
    
    try:
        # Open the file in append mode ('a') to add new checkpoints
        with open(file_path, 'a') as f:
            f.write(checkpoint_entry)
    except IOError as e:
        print(f"Error writing to file '{file_path}': {e}")

# Call the function to perform the task
write_checkpoint()