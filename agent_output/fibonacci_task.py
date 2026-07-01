import os

def fibonacci(n):
    """Return a list of the first n Fibonacci numbers."""
    fib_sequence = []
    a, b = 0, 1
    for _ in range(n):
        fib_sequence.append(a)
        a, b = b, a + b
    return fib_sequence

def main():
    directory = "ciel_workspace"
    os.makedirs(directory, exist_ok=True)
    
    fib_numbers = fibonacci(20)
    
    # Format each number as a separate line
    formatted = "\n".join(str(num) for num in fib_numbers)
    
    filepath = os.path.join(directory, "fib_results.txt")
    with open(filepath, "w") as f:
        f.write(formatted)

if __name__ == "__main__":
    main()