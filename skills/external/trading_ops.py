import os
import requests
import pandas as pd
import pandas_ta as ta
from langchain_core.tools import StructuredTool

TRADING_SYSTEM_PROMPT = """
[CIEL-SENTINEL MARKET INTELLIGENCE]
Role: You are "Ciel-Sentinel", a Market Intelligence module.

[CONTEXT]
- You provide simple, fast market data for Crypto, Forex (EUR/USD), Metals (XAU/USD), and Stocks.
- Crypto detailed stats use the public Binance API (no key needed).
- Global prices (Forex, Metals) require a TwelveData API key.

[INSTRUCTIONS]
1. Respond clearly with current prices or technical stats when asked.
2. Provide simple technical analysis (RSI, Moving Averages, Trend) if requested.

[OUTPUT FORMAT]
* **[Symbol]:** Current Price
* **[Analysis]:** Brief overview of the market status.
"""

# ==========================================
# RAW FUNCTIONS (importable by scheduler.py)
# ==========================================

def fetch_market_price(symbol: str) -> str:
    """Get real-time price. Raw function — usable by scheduler directly."""
    API_KEY = os.getenv("TWELVEDATA_API_KEY")
    
    # If no API key, try to fallback to Binance if it looks like Crypto
    if not API_KEY:
        if "/" not in symbol and symbol.endswith("USDT") or symbol in ["BTC", "ETH", "SOL"]:
            sym = symbol.replace("/", "").replace("-", "")
            if not sym.endswith("USDT"): sym += "USDT"
            try:
                data = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}").json()
                if "price" in data: return f"Giá {sym}: {data['price']} USDT (via Binance free tier)."
            except: pass
        return "Error: Missing TWELVEDATA_API_KEY in .env. Get a free API key at https://twelvedata.com/ (Supports Forex, XAU, Stocks, Crypto)."
    
    try:
        # Format symbol for TwelveData (e.g. EUR/USD)
        if len(symbol) == 6 and "/" not in symbol:
            symbol = f"{symbol[:3]}/{symbol[3:]}"
        
        url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={API_KEY}"
        data = requests.get(url).json()
        if "price" in data: return f"Giá {symbol}: {data['price']}"
        return f"Error: Could not find price for {symbol}. Ensure format is correct (e.g. EUR/USD, XAU/USD, AAPL)."
    except Exception as e: return f"API Error: {e}"


def fetch_crypto_stats(symbol: str) -> str:
    """Get 24h stats for Crypto only (Binance Free API). Raw function."""
    try:
        symbol = symbol.upper().replace("/", "").replace("-", "")
        if not symbol.endswith("USDT"): symbol += "USDT"
        data = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}").json()
        if "lastPrice" in data: return f"Stats {symbol}: Price={data['lastPrice']}, Change={data['priceChangePercent']}%, High={data['highPrice']}, Low={data['lowPrice']}"
        return "Error fetching stats."
    except Exception as e: return f"API Error: {e}"


def fetch_crypto_technical(symbol: str, interval: str = "1h") -> str:
    """Get Technical indicators for Crypto only (Binance Free API). Raw function."""
    try:
        symbol = symbol.upper().replace("/", "").replace("-", "")
        if not symbol.endswith("USDT"): symbol += "USDT"
        data = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=100").json()
        if isinstance(data, dict) and "code" in data: return f"API Error: {data['msg']}"

        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'vol', 'ct', 'qav', 'nt', 'tbv', 'tqv', 'ig'])
        df['close'] = df['close'].astype(float)
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        df['MA_5'] = ta.sma(df['close'], length=5)
        df['MA_30'] = ta.sma(df['close'], length=30)
        
        cp, crsi = df['close'].iloc[-1], df['RSI_14'].iloc[-1]
        ma5, ma30 = df['MA_5'].iloc[-1], df['MA_30'].iloc[-1]
        trend = "TĂNG (Bullish)" if ma5 > ma30 else "GIẢM (Bearish)"
        rsi_status = "TRUNG TÍNH"
        if crsi >= 70: rsi_status = "QUÁ MUA (Overbought)"
        elif crsi <= 30: rsi_status = "QUÁ BÁN (Oversold)"

        return f"Kỹ thuật {symbol} ({interval}): Giá {cp}, RSI: {crsi:.2f} ({rsi_status}), MA(5,30): {ma5:.2f}, {ma30:.2f}, Xu hướng: {trend}"
    except Exception as e: return f"Lỗi tính toán: {e}"


# ==========================================
# FACTORY (standard get_*_tools() pattern)
# ==========================================
def get_trading_tools() -> dict:
    try:
        tools = []
        
        price_tool = StructuredTool.from_function(
            func=fetch_market_price, 
            name="get_market_price", 
            description="Get the real-time price of Forex (EUR/USD), Metals (XAU/USD), Stocks, or Crypto via TwelveData API. YOU MUST USE THIS TOOL when asked for current prices or quotes."
        )

        stats_tool = StructuredTool.from_function(
            func=fetch_crypto_stats, 
            name="get_crypto_stats", 
            description="Get 24-hour statistics (change %, high, low) for CRYPTO ONLY via public Binance API."
        )

        tech_tool = StructuredTool.from_function(
            func=fetch_crypto_technical, 
            name="analyze_crypto_technical", 
            description="Get technical analysis (RSI, MA, Trend) for CRYPTO ONLY via Binance API."
        )

        tools.extend([price_tool, stats_tool, tech_tool])
        return {"tools": tools, "prompt": TRADING_SYSTEM_PROMPT}

    except Exception as e: return {"tools": [], "prompt": ""}