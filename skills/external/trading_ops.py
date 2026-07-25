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
                data = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}", timeout=10).json()
                if "price" in data: return f"Giá {sym}: {data['price']} USDT (via Binance free tier)."
            except: pass
        return "Error: Missing TWELVEDATA_API_KEY in .env. Get a free API key at https://twelvedata.com/ (Supports Forex, XAU, Stocks, Crypto)."
    
    try:
        # Format symbol for TwelveData (e.g. EUR/USD)
        if len(symbol) == 6 and "/" not in symbol:
            symbol = f"{symbol[:3]}/{symbol[3:]}"
        
        url = f"https://api.twelvedata.com/price?symbol={symbol}&apikey={API_KEY}"
        data = requests.get(url, timeout=10).json()
        if "price" in data: return f"Giá {symbol}: {data['price']}"
        return f"Error: Could not find price for {symbol}. Ensure format is correct (e.g. EUR/USD, XAU/USD, AAPL)."
    except Exception as e: return f"API Error: {e}"


def _to_binance_symbol(symbol: str) -> str:
    """Normalize any common spelling of a crypto pair to Binance's BASEUSDT form.

    The old logic stripped the separator and appended USDT unconditionally, so every
    pair written with a USD quote — including `BTC/USD`, the exact style
    `get_market_price` uses for XAU/USD and EUR/USD — became `BTCUSDUSDT` and failed.
    Only a bare ticker ever worked, which is why the Brain kept guessing and the
    self-healing loop burned retries cycling BTC/USDT → BTCUSD → BTCUSDT.
    """
    sym = (symbol or "").upper().replace("/", "").replace("-", "").strip()
    for quote in ("USDT", "USDC", "USD"):        # longest first
        if sym.endswith(quote) and len(sym) > len(quote):
            sym = sym[: -len(quote)]
            break
    return f"{sym}USDT"


def fetch_crypto_stats(symbol: str) -> str:
    """Get 24h stats for Crypto only (Binance Free API). Raw function."""
    try:
        symbol = _to_binance_symbol(symbol)
        data = requests.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}", timeout=10).json()
        if "lastPrice" in data: return f"Stats {symbol}: Price={data['lastPrice']}, Change={data['priceChangePercent']}%, High={data['highPrice']}, Low={data['lowPrice']}"
        return "Error fetching stats."
    except Exception as e: return f"API Error: {e}"


def fetch_crypto_technical(symbol: str, interval: str = "1h") -> str:
    """Get Technical indicators for Crypto only (Binance Free API). Raw function."""
    try:
        symbol = _to_binance_symbol(symbol)
        data = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=100", timeout=10).json()
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

        # Deterministic price-vs-MA facts, stated explicitly so the Worker never has
        # to (mis-)infer this comparison itself when writing the narrative. Observed
        # failure (caught by Middleware): the Worker repeatedly asserted "price is
        # below MA5/MA30, confirming the bearish trend" when price was actually ABOVE
        # both — an arithmetic error an LLM should never have to make when Python can
        # state the fact for free. "Xu hướng" (MA5-vs-MA30 cross) and "vị trí giá"
        # (price-vs-MA) are two DIFFERENT signals — labeled separately to avoid
        # conflating them.
        pos5 = "TRÊN" if cp > ma5 else ("DƯỚI" if cp < ma5 else "BẰNG")
        pos30 = "TRÊN" if cp > ma30 else ("DƯỚI" if cp < ma30 else "BẰNG")

        return (f"Kỹ thuật {symbol} ({interval}): Giá {cp}, RSI: {crsi:.2f} ({rsi_status}), "
                f"MA(5,30): {ma5:.2f}, {ma30:.2f}, Xu hướng (MA5 vs MA30): {trend}. "
                f"Vị trí giá (dùng câu này khi mô tả giá so với MA, đừng tự suy luận): "
                f"giá hiện tại đang ở {pos5} MA5 và {pos30} MA30.")
    except Exception as e: return f"Lỗi tính toán: {e}"


# ==========================================
# MARKET DASHBOARD REPORT (Report.pdf layout -> BTC/XAUUSD)
# ==========================================

def build_market_report_html(
    report_date: str,
    btc_price: str,
    btc_change_pct: str,
    btc_rsi: str,
    btc_trend: str,
    xau_price: str,
    xau_change_pct: str,
    xau_trend: str,
    risk_level: str,
    risk_factors: str,
    conclusion: str,
    xau_rsi: str = "N/A",
) -> str:
    """Build the market dashboard HTML (email_template/market_report.html) for BTC & XAU/USD.

    All values must come from real tool results (get_market_price, get_crypto_stats,
    analyze_crypto_technical). Do NOT invent numbers. Pass "N/A" for anything unavailable.
    Returns the full HTML string to feed into send_gmail_html_message.
    """
    def _color(pct: str) -> str:
        try:
            return "#16a34a" if float(str(pct).replace("%", "").replace("+", "")) >= 0 else "#dc2626"
        except Exception:
            return "#64748b"

    tpl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "email_template", "market_report.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        html = f.read()

    values = {
        "REPORT_DATE": report_date,
        "BTC_PRICE": btc_price,
        "BTC_CHANGE_PCT": btc_change_pct,
        "BTC_CHANGE_COLOR": _color(btc_change_pct),
        "BTC_RSI": btc_rsi,
        "BTC_TREND": btc_trend,
        "XAU_PRICE": xau_price,
        "XAU_CHANGE_PCT": xau_change_pct,
        "XAU_CHANGE_COLOR": _color(xau_change_pct),
        "XAU_RSI": xau_rsi,
        "XAU_TREND": xau_trend,
        "RISK_LEVEL": risk_level,
        "RISK_FACTORS": risk_factors,
        "CONCLUSION": conclusion,
    }
    for k, v in values.items():
        html = html.replace("{{" + k + "}}", str(v))
    return html


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

        report_tool = StructuredTool.from_function(
            func=build_market_report_html,
            name="build_market_report_html",
            description="Build a styled HTML market dashboard report (Report.pdf layout) for BTC & XAU/USD. Fill every field ONLY with real values from get_market_price / get_crypto_stats / analyze_crypto_technical; use 'N/A' if missing. Returns full HTML to pass into send_gmail_html_message.",
        )

        tools.extend([price_tool, stats_tool, tech_tool, report_tool])
        return {"tools": tools, "prompt": TRADING_SYSTEM_PROMPT}

    except Exception as e: return {"tools": [], "prompt": ""}