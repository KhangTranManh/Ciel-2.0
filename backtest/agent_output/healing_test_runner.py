import os
import subprocess
import sys

# Ensure workspace directory exists
os.makedirs("ciel_workspace", exist_ok=True)

# 1. Write buggy script
buggy_code = """import nonexistent_module

def divide(a, b):
    return a / b

print(divide(10, 0))
"""

buggy_path = "ciel_workspace/buggy_healing_test.py"
with open(buggy_path, "w") as f:
    f.write(buggy_code)

# 2. Run buggy script to show errors
print("=== Running buggy script ===")
result = subprocess.run([sys.executable, buggy_path], capture_output=True, text=True)
print(result.stdout)
print(result.stderr)

# 3. Write fixed version
fixed_code = """def divide(a, b):
    if b == 0:
        return "Error: division by zero"
    return a / b

print(divide(10, 2))
print(divide(10, 0))
"""

fixed_path = "ciel_workspace/fixed_healing_test.py"
with open(fixed_path, "w") as f:
    f.write(fixed_code)

# 4. Run fixed script to confirm it works
print("=== Running fixed script ===")
result = subprocess.run([sys.executable, fixed_path], capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print(result.stderr)