import os

def fibonacci(n):
    if n <= 0:
        return []
    if n == 1:
        return [0]
    fib = [0, 1]
    for i in range(2, n):
        fib.append(fib[-1] + fib[-2])
    return fib

if __name__ == "__main__":
    result = fibonacci(10)
    print(result)
    line = f"Fibonacci(10): {result}\n"
    dir_path = "ciel_workspace"
    os.makedirs(dir_path, exist_ok=True)
    file_path = os.path.join(dir_path, "fib_results.txt")
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(line)