def derivative(f, x, h=1e-6):
    """
    Tính đạo hàm của hàm f tại điểm x bằng phương pháp sai phân hữu hạn.
    """
    return (f(x + h) - f(x - h)) / (2 * h)

if __name__ == "__main__":
    # Ví dụ: đạo hàm của f(x) = x^2 tại x = 3
    f = lambda x: x**2
    print(derivative(f, 3))