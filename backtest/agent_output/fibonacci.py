def fibonacci(n: int) -> int:
    """
    Calculates the nth Fibonacci number.

    The Fibonacci sequence starts with F(0) = 0 and F(1) = 1.
    For n > 1, F(n) = F(n-1) + F(n-2).

    Args:
        n: A non-negative integer representing the index in the Fibonacci sequence.

    Returns:
        The nth Fibonacci number.

    Raises:
        ValueError: If n is a negative integer.
    """
    if n < 0:
        raise ValueError("Input must be a non-negative integer.")
    elif n == 0:
        return 0
    elif n == 1:
        return 1
    else:
        a, b = 0, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b