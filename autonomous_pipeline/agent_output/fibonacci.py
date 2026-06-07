def compute_fibonacci_numbers(count):
    """
    Computes the first 'count' Fibonacci numbers.

    Args:
        count (int): The number of Fibonacci numbers to compute.

    Returns:
        list: A list containing the first 'count' Fibonacci numbers.
    """
    if count <= 0:
        return []
    elif count == 1:
        return [0]
    else:
        fib_numbers = [0, 1]
        while len(fib_numbers) < count:
            next_fib = fib_numbers[-1] + fib_numbers[-2]
            fib_numbers.append(next_fib)
        return fib_numbers

if __name__ == "__main__":
    num_fibs = 20
    fibonacci_sequence = compute_fibonacci_numbers(num_fibs)
    for number in fibonacci_sequence:
        print(number)