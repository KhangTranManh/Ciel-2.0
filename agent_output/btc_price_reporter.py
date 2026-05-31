import requests
import os

def fetch_btc_price_data():
    """
    Fetches the last 24 hours of BTC/USDT price data from Binance API.
    Returns a list of kline data or None if an error occurs.
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1h",  # 1-hour candlesticks
        "limit": 24        # Last 24 candlesticks
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from Binance API: {e}")
        return None

def calculate_metrics(klines):
    """
    Calculates high, low, and percentage change from kline data.
    """
    if not klines:
        return None, None, None

    # kline data format: [open_time, open, high, low, close, ...]
    # Indices: 1=open, 2=high, 3=low, 4=close

    high_prices = [float(kline[2]) for kline in klines]
    low_prices = [float(kline[3]) for kline in klines]

    max_high = max(high_prices)
    min_low = min(low_prices)

    first_open_price = float(klines[0][1])
    last_close_price = float(klines[-1][4])

    if first_open_price == 0:
        percentage_change = 0.0
    else:
        percentage_change = ((last_close_price - first_open_price) / first_open_price) * 100

    return max_high, min_low, percentage_change

def generate_summary_report(max_high, min_low, percentage_change):
    """
    Generates a concise summary report string.
    """
    if max_high is None or min_low is None or percentage_change is None:
        return "Failed to generate BTC 24h summary due to data retrieval or calculation errors."

    report = (
        "--- BTC/USD 24-Hour Price Summary ---\n"
        f"High: ${max_high:,.2f}\n"
        f"Low: ${min_low:,.2f}\n"
        f"Percentage Change: {percentage_change:+.2f}%\n"
        "-------------------------------------"
    )
    return report

def main():
    output_dir = 'autonomous_pipeline/agent_output'
    output_file_path = os.path.join(output_dir, 'btc_24h_summary.txt')

    # Ensure the output directory exists
    os.makedirs(output_dir, exist_ok=True)

    klines = fetch_btc_price_data()

    if klines:
        max_high, min_low, percentage_change = calculate_metrics(klines)
        summary_report = generate_summary_report(max_high, min_low, percentage_change)
    else:
        summary_report = "Failed to retrieve BTC price data for the last 24 hours."

    # Print to console
    print(summary_report)

    # Write to file
    try:
        with open(output_file_path, 'w') as f:
            f.write(summary_report)
        print(f"\nSummary report successfully written to '{output_file_path}'")
    except IOError as e:
        print(f"Error writing summary report to file: {e}")

if __name__ == "__main__":
    main()