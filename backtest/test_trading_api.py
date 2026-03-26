import os
import time
import hmac
import hashlib
import requests
from dotenv import load_dotenv
from colorama import Fore, Style, init

init(autoreset=True)
load_dotenv()

API_KEY = os.getenv("MEXC_API_KEY")
SECRET_KEY = os.getenv("MEXC_API_SECRET")

def get_contract_info(symbol):
    """Kéo Giá hợp lý (Fair Price) và Hệ số hợp đồng (Contract Size) từ Public API"""
    try:
        # Lấy contract size
        detail_url = f"https://contract.mexc.com/api/v1/contract/detail?symbol={symbol}"
        contract_size = requests.get(detail_url).json().get('data', {}).get('contractSize', 1)

        # Lấy fair price
        ticker_url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={symbol}"
        fair_price = requests.get(ticker_url).json().get('data', {}).get('fairPrice', 0)

        return float(contract_size), float(fair_price)
    except:
        return 1, 0

def test_mexc_full_portfolio():
    print(Fore.CYAN + "=== [CIEL SYSTEM: RADAR QUÉT TOÀN BỘ TÀI SẢN MEXC] ===" + Style.RESET_ALL)
    
    if not API_KEY or not SECRET_KEY:
        print(Fore.RED + "[-] LỖI CHÍ MẠNG: Không tìm thấy Key trong file .env" + Style.RESET_ALL)
        return

    # ==========================================
    # 1. QUÉT TÀI SẢN SPOT
    # ==========================================
    print(Fore.CYAN + "\n--- [1. TÀI SẢN SPOT (VÍ GIAO NGAY)] ---" + Style.RESET_ALL)
    try:
        timestamp = str(int(time.time() * 1000))
        query_string = f"recvWindow=10000&timestamp={timestamp}"
        signature_spot = hmac.new(SECRET_KEY.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        
        resp_spot = requests.get(
            f"https://api.mexc.com/api/v3/account?{query_string}&signature={signature_spot}", 
            headers={"X-MEXC-APIKEY": API_KEY, "Content-Type": "application/json"}
        )
        
        if resp_spot.status_code == 200:
            balances = [b for b in resp_spot.json().get('balances', []) if float(b['free']) > 0 or float(b['locked']) > 0]
            for b in balances:
                print(Fore.GREEN + f"  + {b['asset']}: {b['free']} (Khả dụng)" + Style.RESET_ALL)
    except Exception as e:
        print(Fore.RED + f"  [-] Lỗi: {e}" + Style.RESET_ALL)

    # ==========================================
    # 2. QUÉT VÍ FUTURES & PNL
    # ==========================================
    print(Fore.CYAN + "\n--- [2. VÍ FUTURES (PHÁI SINH)] ---" + Style.RESET_ALL)
    try:
        req_time = str(int(time.time() * 1000))
        signature_fut = hmac.new(SECRET_KEY.encode('utf-8'), (API_KEY + req_time).encode('utf-8'), hashlib.sha256).hexdigest()
        headers_fut = {"ApiKey": API_KEY, "Request-Time": req_time, "Signature": signature_fut, "Content-Type": "application/json"}

        # A. Số dư ví Futures
        resp_fut_assets = requests.get("https://contract.mexc.com/api/v1/private/account/assets", headers=headers_fut)
        if resp_fut_assets.status_code == 200:
            for a in resp_fut_assets.json().get('data', []):
                if float(a.get('availableBalance', 0)) > 0 or float(a.get('positionMargin', 0)) > 0:
                    print(Fore.GREEN + f"  + Ví Futures ({a['currency']}): {a['availableBalance']} (Khả dụng) | Ký quỹ: {a['positionMargin']}" + Style.RESET_ALL)

        # B. Phân tích Vị thế và Tính toán PNL
        print(Fore.CYAN + "\n--- [3. CÁC VỊ THẾ ĐANG MỞ TRÊN CHIẾN TRƯỜNG] ---" + Style.RESET_ALL)
        resp_fut_pos = requests.get("https://contract.mexc.com/api/v1/private/position/open_positions", headers=headers_fut)
        
        if resp_fut_pos.status_code == 200:
            positions = resp_fut_pos.json().get('data', [])
            if positions:
                for pos in positions:
                    symbol = pos.get('symbol')
                    pos_type = pos.get('positionType')
                    side = Fore.GREEN + "LONG 🟢" if pos_type == 1 else Fore.RED + "SHORT 🔴"
                    leverage = pos.get('leverage')
                    volume = float(pos.get('holdVol', 0)) # Số contract
                    entry_price = float(pos.get('holdAvgPrice', 0))
                    margin = float(pos.get('im', 0)) # Ký quỹ (Isolated)
                    liq_price = pos.get('liquidatePrice', 'N/A')
                    
                    # 1. Kéo dữ liệu thời gian thực
                    contract_size, fair_price = get_contract_info(symbol)
                    
                    # 2. Làm toán giống MEXC App
                    entry_value = volume * contract_size * entry_price
                    current_value = volume * contract_size * fair_price
                    
                    if pos_type == 1: # LONG
                        pnl = current_value - entry_value
                    else: # SHORT
                        pnl = entry_value - current_value
                        
                    pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED
                    pnl_percent = (pnl / margin * 100) if margin > 0 else 0

                    print(f"  ⚡ {symbol} | {side} {Style.RESET_ALL}| Đòn bẩy: {leverage}x")
                    print(f"     Giá trị vị thế (USDT): {current_value:.4f}")
                    print(f"     Ký quỹ (Margin): {margin:.4f} USDT")
                    print(f"     Giá vào lệnh: {entry_price} | Giá hợp lý: {fair_price} | Giá thanh lý: {liq_price}")
                    print(f"     Lãi/Lỗ (PNL): {pnl_color}{pnl:.4f} USDT ({pnl_percent:.2f}%){Style.RESET_ALL}\n")
            else:
                print(Fore.YELLOW + "  [*] Báo cáo: Ngài không có vị thế nào đang mở." + Style.RESET_ALL)
    except Exception as e:
         print(Fore.RED + f"  [-] Lỗi hệ thống: {e}" + Style.RESET_ALL)

    print(Fore.CYAN + "=======================================================" + Style.RESET_ALL)

if __name__ == "__main__":
    test_mexc_full_portfolio()