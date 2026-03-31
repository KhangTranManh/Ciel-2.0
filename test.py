import os
import sys
import time
from pathlib import Path

# Đảm bảo Python nhận diện được các module trong dự án
sys.path.append(str(Path(__file__).resolve().parent))

from core.agent_loop import AgentLoop

def run_backtest():
    print("⚡ [Hệ Thống]: Đang khởi động Lõi Ciel cho Chiến dịch Backtest Toàn Diện...")
    
    try:
        ciel_agent = AgentLoop()
    except Exception as e:
        print(f"❌ [Lỗi Khởi Tạo]: Không thể bật Lõi Ciel. Lỗi: {e}")
        return

    base_dir = Path(__file__).resolve().parent
    log_file = base_dir / "ciel_data" / "logs" / "full_backtest_report.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # =====================================================================
    # KỊCH BẢN DUYỆT BINH (QUÉT TOÀN BỘ VŨ KHÍ TỪ TOOLMANAGER)
    # =====================================================================
    test_cases = [
        # --- 1. PHÂN HỆ BỘ NHỚ (Memory Ops) ---
        ("MEMORY - Lưu trữ", "Ciel, hãy nhớ kỹ thông tin này: 'Mã dự án hiện tại là OMEGA-99'."),
        ("MEMORY - Truy vấn", "Ciel, mã dự án hiện tại mà ta vừa bảo mi nhớ là gì?"),
        ("MEMORY - Xóa", "Ciel, hãy xóa thông tin về mã dự án OMEGA-99 khỏi bộ nhớ."),

        # --- 2. PHÂN HỆ HỆ THỐNG (System Ops - Quarantine Zone) ---
        ("SYSTEM - Tạo file", "Tạo file '123.txt' trong workspace và ghi chữ 'Mục tiêu khóa mục tiêu' vào đó."),
        ("SYSTEM - Ghi nối", "Ghi nối thêm dòng 'Đã xác nhận an toàn' vào cuối file 'backtest_target.txt'."),
        ("SYSTEM - Lấy thông tin", "Kiểm tra thông tin (dung lượng, loại tệp) của file 'backtest_target.txt'."),
        ("SYSTEM - Lập trình", "Viết một file 'loop_test.py' để in ra các số từ 1 đến 5, sau đó chạy nó."),
        ("SYSTEM - Liệt kê", "Liệt kê toàn bộ file và thư mục hiện có trong workspace cho ta xem."),
        ("SYSTEM - Dọn dẹp", "Xóa file 'backtest_target.txt' và 'loop_test.py' đi."),

        # --- 3. PHÂN HỆ TÌNH BÁO (Gmail Ops) ---
        # Lưu ý: Chỉ test Đọc và Tạo Draft để tránh hệ thống tự động gửi email rác
        ("GMAIL - Tìm kiếm", "Kiểm tra xem hộp thư của ta có email nào gửi từ 'Google' hoặc có tiêu đề 'Security' gần đây không."),
        ("GMAIL - Tạo Nháp", "Tạo một bản nháp email (draft) gửi cho 'test@example.com' với tiêu đề 'Báo cáo Hệ thống' và nội dung 'Hệ thống Ciel đã online 100%'."),

        # --- 4. PHÂN HỆ TÀI CHÍNH (Trading Ops) ---
        ("TRADING - Giá Realtime", "Kiểm tra giá hiện tại của đồng BTC và ETH cho ta."),
        ("TRADING - Thống kê 24h", "Lấy thống kê biến động 24h qua của đồng SOL."),
        ("TRADING - Phân tích kỹ thuật", "Dùng toán học phân tích kỹ thuật chỉ số của đồng BNB khung 1h. Báo cáo xu hướng và RSI."),
        ("TRADING - Radar Danh mục", "Quét toàn bộ tài sản Spot và các vị thế Futures đang mở trên sàn MEXC của ta.")
    ]
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("="*80 + "\n")
        f.write(f"BÁO CÁO DUYỆT BINH & KIỂM THỬ TỔNG LỰC CIEL SYSTEM (V2)\n")
        f.write(f"Thời gian bắt đầu: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*80 + "\n\n")
        
    print(f"\n🚀 Bắt đầu bắn {len(test_cases)} lệnh thử nghiệm. Log sẽ lưu tại: {log_file.name}\n")
    print("-" * 60)
    
    for name, prompt in test_cases:
        print(f"▶ Đang thực thi: {name}")
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{name}]\n")
            f.write(f"MASTER : {prompt}\n")
        
        try:
            # GỌI LÕI CIEL XỬ LÝ
            response = ciel_agent.run_step(prompt)
            
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"CIEL   : {response}\n")
                f.write("-" * 80 + "\n\n")
                
        except Exception as e:
            error_msg = f"[CRITICAL ERROR]: Lõi hệ thống sụp đổ - {str(e)}"
            print(f"❌ {error_msg}")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"CIEL   : {error_msg}\n")
                f.write("-" * 80 + "\n\n")
                
        # Nghỉ 4 giây giữa các lệnh để VRAM xả nhiệt và không bị dính Rate Limit của API (Binance, Gmail)
        time.sleep(10) 
        
    print("\n" + "="*60)
    print(f"✅ CHIẾN DỊCH BACKTEST TỔNG LỰC HOÀN TẤT!")
    print(f"Ngài hãy mở file: '{log_file}' để nghiệm thu khả năng dùng Tool của Ciel.")

if __name__ == "__main__":
    run_backtest()