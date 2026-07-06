import os
import sys

try:
    result = 1 / 0
    print(result)
except ZeroDivisionError:
    print("Error: Division by zero is not allowed. Please check the divisor.")