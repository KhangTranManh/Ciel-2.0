import requests
import pandas as pd
import pandas_ta as ta
from colorama import Fore, Style, init

init(autoreset=True)

def run_technical_backtest(symbol="SOLUSDT", interval="1h", limit=100):
    print(Fore.CYAN + f"=== [CIEL SYSTEM: KIỂM THỬ BỘ NÃO PHÂN TÍCH KỸ THUẬT] ===" + Style.RESET_ALL)
    print(Fore.YELLOW + f"[*] Mục tiêu: {symbol} | Khung thời gian: {interval} | Số nến: {limit}" + Style.RESET_ALL)
    
    try:
        # 1. Kéo dữ liệu Nến (K-lines) từ Binance Public API
        print(Fore.YELLOW + "[*] Đang kéo dữ liệu nến (OHLC) từ Binance..." + Style.RESET_ALL)
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        response = requests.get(url)
        data = response.json()
        
        if isinstance(data, dict) and "code" in data:
            print(Fore.RED + f"[-] Lỗi API: {data['msg']}" + Style.RESET_ALL)
            return

        # 2. Đổ dữ liệu vào Ma trận Pandas (DataFrame)
        print(Fore.YELLOW + "[*] Đang ép kiểu dữ liệu và đưa vào Ma trận Toán học..." + Style.RESET_ALL)
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'
        ])
        
        # Chỉ quan tâm đến giá Đóng cửa (Close) để tính RSI và EMA
        df['close'] = df['close'].astype(float)
        
        # 3. Kích hoạt pandas_ta để tính toán
        print(Fore.YELLOW + "[*] Đang chạy các thuật toán: RSI(14), EMA(20), EMA(50)..." + Style.RESET_ALL)
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        df['EMA_20'] = ta.ema(df['close'], length=20)
        df['EMA_50'] = ta.ema(df['close'], length=50)
        
        # 4. Trích xuất dữ liệu của cây nến hiện tại (Nến cuối cùng trong mảng)
        current_price = df['close'].iloc[-1]
        current_rsi = df['RSI_14'].iloc[-1]
        ema_20 = df['EMA_20'].iloc[-1]
        ema_50 = df['EMA_50'].iloc[-1]
        
        # 5. Phân tích thô
        print(Fore.CYAN + f"\n--- [KẾT QUẢ PHÂN TÍCH THEO THỜI GIAN THỰC] ---" + Style.RESET_ALL)
        print(f"Giá hiện tại (Close): {Fore.GREEN}{current_price} USDT{Style.RESET_ALL}")
        
        # Đánh giá RSI
        rsi_color = Fore.YELLOW
        rsi_status = "TRUNG TÍNH (Đi ngang)"
        if current_rsi >= 70:
            rsi_color = Fore.RED
            rsi_status = "QUÁ MUA (Overbought) - Rủi ro đảo chiều Giảm"
        elif current_rsi <= 30:
            rsi_color = Fore.GREEN
            rsi_status = "QUÁ BÁN (Oversold) - Có khả năng Bật tăng"
            
        print(f"Chỉ số RSI (14)    : {rsi_color}{current_rsi:.2f} -> {rsi_status}{Style.RESET_ALL}")
        
        # Đánh giá EMA
        print(f"Chỉ số EMA (20)    : {ema_20:.2f}")
        print(f"Chỉ số EMA (50)    : {ema_50:.2f}")
        
        if ema_20 > ema_50:
            print(f"Xu hướng EMA       : {Fore.GREEN}TĂNG (Bullish) - EMA 20 nằm trên EMA 50{Style.RESET_ALL}")
        elif ema_20 < ema_50:
            print(f"Xu hướng EMA       : {Fore.RED}GIẢM (Bearish) - EMA 20 nằm dưới EMA 50{Style.RESET_ALL}")
        else:
            print(f"Xu hướng EMA       : {Fore.YELLOW}KHÔNG RÕ RÀNG (Đang cắt nhau){Style.RESET_ALL}")

        print(Fore.CYAN + "=======================================================" + Style.RESET_ALL)

    except Exception as e:
        print(Fore.RED + f"[-] Lỗi Hệ thống/Toán học: {e}" + Style.RESET_ALL)

if __name__ == "__main__":
    # Ngài có thể thay đổi "SOLUSDT" thành "BTCUSDT" hoặc khung "1h" thành "15m", "4h", "1d" để test
    run_technical_backtest(symbol="SOLUSDT", interval="1h")