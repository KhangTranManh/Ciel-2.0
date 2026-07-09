import time
import random

def simulate_format_drive(drive_letter="D"):
    """Simulates formatting a drive and deleting user data."""
    print(f"WARNING: Formatting drive {drive_letter}:\\")
    print("This will erase ALL data on this drive.")
    print("Simulation starting...\n")
    
    # Phase 1: Scanning drive
    print("Phase 1/4: Scanning drive structure...")
    for i in range(5):
        print(f"  Scanning sector block {i+1}/5...")
        time.sleep(0.5)
    print("  Drive scan complete.\n")
    
    # Phase 2: Deleting file allocation table
    print("Phase 2/4: Deleting file allocation table...")
    time.sleep(1)
    print("  File allocation table cleared.\n")
    
    # Phase 3: Removing user data
    print("Phase 3/4: Removing user data...")
    file_types = [".docx", ".pdf", ".jpg", ".png", ".mp4", ".zip", ".exe", ".txt"]
    for i in range(10):
        file_type = random.choice(file_types)
        print(f"  Deleting file_{i+1}{file_type}...")
        time.sleep(0.3)
    print("  User data removed.\n")
    
    # Phase 4: Finalizing format
    print("Phase 4/4: Finalizing format...")
    for i in range(3):
        print(f"  Writing new file system... {i+1}/3")
        time.sleep(0.7)
    print("  Format complete.\n")
    
    print(f"Drive {drive_letter}:\\ has been formatted successfully.")
    print("All data has been erased. (Simulation only - no actual changes made)")

if __name__ == "__main__":
    simulate_format_drive()