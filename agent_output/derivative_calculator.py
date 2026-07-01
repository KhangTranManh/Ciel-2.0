import sympy as sp

def tinh_dao_ham():
    """
    Hàm chính: nhận biểu thức và biến từ người dùng, tính đạo hàm bậc 1 và bậc 2.
    """
    while True:
        print("\n=== TÍNH ĐẠO HÀM BẰNG SYMPY ===")
        bieu_thuc_str = input("Nhập biểu thức (ví dụ: x**3 + 2*x**2) hoặc 'q' để thoát: ").strip()
        if bieu_thuc_str.lower() == 'q':
            print("Kết thúc chương trình.")
            break

        bien_str = input("Nhập biến (ví dụ: x): ").strip()
        if not bien_str:
            print("Biến không được để trống. Vui lòng nhập lại.")
            continue

        try:
            # Chuyển đổi biểu thức và biến thành đối tượng sympy
            x = sp.Symbol(bien_str)
            bieu_thuc = sp.sympify(bieu_thuc_str)

            # Tính đạo hàm bậc 1
            dao_ham_1 = sp.diff(bieu_thuc, x)
            # Tính đạo hàm bậc 2
            dao_ham_2 = sp.diff(bieu_thuc, x, 2)

            # In kết quả
            print(f"\nBiểu thức: {bieu_thuc}")
            print(f"Đạo hàm bậc 1 theo {bien_str}: {dao_ham_1}")
            print(f"Đạo hàm bậc 2 theo {bien_str}: {dao_ham_2}")

        except sp.SympifyError:
            print("Lỗi: Biểu thức không hợp lệ. Vui lòng kiểm tra cú pháp.")
        except Exception as e:
            print(f"Lỗi không xác định: {e}")

if __name__ == "__main__":
    tinh_dao_ham()