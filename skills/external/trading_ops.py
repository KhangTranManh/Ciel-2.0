import os
import time
import hmac
import hashlib
import requests
import pandas as pd
import pandas_ta as ta
from langchain_core.tools import StructuredTool

TRADING_SYSTEM_PROMPT = """
[CIEL-SENTINEL TRADING AGENT & MARKET INTELLIGENCE]
Role: Bạn là "Ciel-Sentinel", một Chuyên gia phân tích kỹ thuật và Giám sát rủi ro.

[CONTEXT]
- Vốn hiện tại: 52.5 USDT. Mục tiêu: 100 USDT (Lãi 5 USDT/ngày). Sàn sử dụng: MEXC và BingX.

[INSTRUCTIONS & RISK SENTINEL - STRICT]
1. Phân tích: Xác định xu hướng, MA (5, 10, 30, 60), râu nến.
2. Bonus Washing: Nếu lệnh có lãi dùng bonus, dời SL về Break-even.
3. Quản trị rủi ro: Lỗ $3 -> BÁO NGỪNG. Lãi $5 -> BÁO CHỐT SỔ.

[OUTPUT FORMAT]
* **[Status]:** (Đang thắng / Đang rủi ro / Chờ đợi)
* **[Chart Analysis]:** Phân tích biểu đồ.
* **[Tactical Advice]:** Lời khuyên vào lệnh / SL / TP.
* **[Risk Warning]:** Cảnh báo quản lý vốn.
* **[Goal Tracker]:** Tiến độ: 52.5 / 100 USDT.
"""

def get_contract_info(symbol: str):
    try:
        size_url = f"https://contract.mexc.com/api/v1/contract/detail?symbol={symbol}"
        contract_size = requests.get(size_url).json().get('data', {}).get('contractSize', 1)
        price_url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={symbol}"
        fair_price = requests.get(price_url).json().get('data', {}).get('fairPrice', 0)
        return float(contract_size), float(fair_price)
    except: return 1, 0

def get_trading_tools() -> dict:
    try:
        tools = []
        
        def get_crypto_price(symbol: str) -> str:
            try:
                symbol = symbol.upper().replace("/", "").replace("-", "")
                data = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}").json()
                if "price" in data: return f"Giá {symbol}: {data['price']} USDT."
                return f"Error: Could not find price for {symbol}."
            except Exception as e: return f"API Error: {e}"
        price_tool = StructuredTool.from_function(func=get_crypto_price, name="get_crypto_price", description="Get the real-time price of a cryptocurrency. YOU MUST USE THIS TOOL when asked for current coin prices or quotes.")

        def get_24h_stats(symbol: str) -> str:
            try:
                symbol = symbol.upper().replace("/", "").replace("-", "")
                data = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}").json()
                if "lastPrice" in data: return f"Stats {symbol}: Price={data['lastPrice']}, Change={data['priceChangePercent']}%, High={data['highPrice']}, Low={data['lowPrice']}"
                return "Error fetching stats."
            except Exception as e: return f"API Error: {e}"
        stats_tool = StructuredTool.from_function(func=get_24h_stats, name="get_24h_stats", description="Get 24-hour statistics (change %, high, low) for a crypto. YOU MUST USE THIS TOOL when asked for daily changes or high/low stats.")

        def analyze_technical_indicators(symbol: str, interval: str = "1h") -> str:
            try:
                symbol = symbol.upper().replace("/", "").replace("-", "")
                data = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=100").json()
                if isinstance(data, dict) and "code" in data: return f"API Error: {data['msg']}"

                df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol', 'ct', 'qav', 'nt', 'tbv', 'tqv', 'ig'])
                df['close'] = df['close'].astype(float)
                df['RSI_14'] = ta.rsi(df['close'], length=14)
                df['MA_5'] = ta.sma(df['close'], length=5)
                df['MA_10'] = ta.sma(df['close'], length=10)
                df['MA_30'] = ta.sma(df['close'], length=30)
                df['MA_60'] = ta.sma(df['close'], length=60)
                
                cp, crsi = df['close'].iloc[-1], df['RSI_14'].iloc[-1]
                ma5, ma10, ma30, ma60 = df['MA_5'].iloc[-1], df['MA_10'].iloc[-1], df['MA_30'].iloc[-1], df['MA_60'].iloc[-1]
                trend = "TĂNG (Bullish)" if ma5 > ma30 else "GIẢM (Bearish)"
                rsi_status = "TRUNG TÍNH"
                if crsi >= 70: rsi_status = "QUÁ MUA (Overbought)"
                elif crsi <= 30: rsi_status = "QUÁ BÁN (Oversold)"

                return (f"Kỹ thuật {symbol} ({interval}): Giá {cp}, RSI: {crsi:.2f} ({rsi_status}), MA(5,10,30,60): {ma5:.2f}, {ma10:.2f}, {ma30:.2f}, {ma60:.2f}, Xu hướng: {trend}")
            except Exception as e: return f"Lỗi tính toán: {e}"
        tech_tool = StructuredTool.from_function(func=analyze_technical_indicators, name="analyze_technical_indicators", description="Get technical analysis (RSI, MA, Trend). YOU MUST USE THIS TOOL when asked for technical indicators or trend of a coin.")

        def get_mexc_portfolio() -> str:
            API_KEY = os.getenv("MEXC_API_KEY")
            SECRET_KEY = os.getenv("MEXC_API_SECRET")
            if not API_KEY or not SECRET_KEY: return "Lỗi: Chưa cấu hình MEXC_API_KEY và SECRET."
            report = ""
            try:
                ts = str(int(time.time() * 1000))
                qs = f"recvWindow=10000&timestamp={ts}"
                sig_spot = hmac.new(SECRET_KEY.encode('utf-8'), qs.encode('utf-8'), hashlib.sha256).hexdigest()
                resp_spot = requests.get(f"https://api.mexc.com/api/v3/account?{qs}&signature={sig_spot}", headers={"X-MEXC-APIKEY": API_KEY, "Content-Type": "application/json"})
                
                report += "[TÀI SẢN SPOT]\n"
                if resp_spot.status_code == 200:
                    bals = [b for b in resp_spot.json().get('balances', []) if float(b['free']) > 0 or float(b['locked']) > 0]
                    for b in bals: report += f"- {b['asset']}: {b['free']} Khả dụng\n"
                    if not bals: report += "- Ví Spot trống.\n"

                req_time = str(int(time.time() * 1000))
                sig_fut = hmac.new(SECRET_KEY.encode('utf-8'), (API_KEY + req_time).encode('utf-8'), hashlib.sha256).hexdigest()
                head_fut = {"ApiKey": API_KEY, "Request-Time": req_time, "Signature": sig_fut, "Content-Type": "application/json"}
                
                report += "\n[TÀI SẢN FUTURES]\n"
                resp_fut_assets = requests.get("https://contract.mexc.com/api/v1/private/account/assets", headers=head_fut)
                if resp_fut_assets.status_code == 200:
                    for a in resp_fut_assets.json().get('data', []):
                        if float(a.get('availableBalance', 0)) > 0 or float(a.get('positionMargin', 0)) > 0:
                            report += f"- {a['currency']}: {a['availableBalance']} Khả dụng | {a['positionMargin']} Ký quỹ\n"

                report += "\n[VỊ THẾ ĐANG MỞ]\n"
                resp_pos = requests.get("https://contract.mexc.com/api/v1/private/position/open_positions", headers=head_fut)
                if resp_pos.status_code == 200:
                    positions = resp_pos.json().get('data', [])
                    if positions:
                        for pos in positions:
                            sym, side, lev = pos.get('symbol'), "LONG" if pos.get('positionType') == 1 else "SHORT", pos.get('leverage')
                            vol, ep, margin = float(pos.get('holdVol', 0)), float(pos.get('holdAvgPrice', 0)), float(pos.get('im', 0))
                            c_size, f_price = get_contract_info(sym)
                            e_val, c_val = vol * c_size * ep, vol * c_size * f_price
                            pnl = (c_val - e_val) if side == "LONG" else (e_val - c_val)
                            pnl_pct = (pnl / margin * 100) if margin > 0 else 0
                            report += f"⚡ {sym} | {side} | Đòn bẩy: {lev}x\n   Entry: {ep} | Giá hợp lý: {f_price}\n   Giá trị: {c_val:.4f} USDT | PNL: {pnl:.4f} USDT ({pnl_pct:.2f}%)\n"
                    else: report += "- Không có vị thế nào đang mở.\n"
                return report
            except Exception as e: return f"Lỗi khi lấy dữ liệu MEXC: {e}"
        portfolio_tool = StructuredTool.from_function(func=get_mexc_portfolio, name="get_mexc_portfolio", description="Get the Master's MEXC portfolio, including Spot balances, Futures assets, and open positions with PNL. YOU MUST USE THIS TOOL when asked to check portfolio or scan assets.")

        tools.extend([price_tool, stats_tool, tech_tool, portfolio_tool])
        return {"tools": tools, "prompt": TRADING_SYSTEM_PROMPT}

    except Exception as e: return {"tools": [], "prompt": ""}