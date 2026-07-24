import os
import sys

def main():
    numbers = [10, 20, 30]
    divisor = 0
    if divisor == 0:
        print("Error: Division by zero is not allowed.")
        sys.exit(1)
    result = sum(numbers) / divisor
    print("Result:", result)

if __name__ == "__main__":
    main()