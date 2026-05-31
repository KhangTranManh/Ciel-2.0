import os

file_path = 'autonomous_pipeline/agent_output/evening_report.txt'
status = 'FAIL'
file_size = 0
content = ""
lines = []

try:
    if os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
        with open(file_path, 'r') as f:
            lines = f.readlines()
            content = "".join(lines)

        num_lines_ok = len(lines) >= 5
        price_deviation_ok = 'price deviation' in content.lower()
        recommendation_ok = 'recommendation' in content.lower()

        if num_lines_ok and price_deviation_ok and recommendation_ok:
            status = 'PASS'
    else:
        print(f"FAIL: File not found at {file_path}")
        exit()

except FileNotFoundError:
    print(f"FAIL: File not found at {file_path}")
    exit()
except Exception as e:
    print(f"FAIL: An error occurred - {e}")
    exit()

print(f"{status}: File size {file_size} bytes")