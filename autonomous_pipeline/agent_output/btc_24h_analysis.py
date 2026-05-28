import requests
import sys

def get_bitcoin_24h_data():
    """
    Retrieves Bitcoin's 24-hour historical price data from CoinGecko API.
    Returns a list of [timestamp, price] pairs or None on error.
    """
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    params = {
        "vs_currency": "usd",
        "days": "1"
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()
        return data.get('prices')
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from CoinGecko API: {e}", file=sys.stderr)
        return None
    except ValueError as e:
        print(f"Error parsing JSON response: {e}", file=sys.stderr)
        return None

def calculate_price_metrics(price_data):
    """
    Calculates opening price, closing price, high, low, and percentage change
    from a list of [timestamp, price] pairs.
    Returns a dictionary of metrics or None if data is insufficient.
    """
    if not price_data or len(price_data) < 2:
        print("Insufficient price data to calculate metrics.", file=sys.stderr)
        return None

    opening_price = price_data[0][1]
    closing_price = price_data[-1][1]
    
    prices_only = [item[1] for item in price_data]
    high_price = max(prices_only)
    low_price = min(prices_only)

    if opening_price == 0:
        percentage_change = 0.0
    else:
        percentage_change = ((closing_price - opening_price) / opening_price) * 100

    return {
        "opening_price": opening_price,
        "closing_price": closing_price,
        "high_price": high_price,
        "low_price": low_price,
        "percentage_change": percentage_change
    }

def main():
    """
    Main function to retrieve data, calculate metrics, and print the report.
    """
    print("Fetching Bitcoin 24-hour historical data...")
    price_data = get_bitcoin_24h_data()

    if price_data is None:
        print("Failed to retrieve Bitcoin price data. Exiting.", file=sys.stderr)
        sys.exit(1)

    metrics = calculate_price_metrics(price_data)

    if metrics is None:
        print("Failed to calculate price metrics. Exiting.", file=sys.stderr)
        sys.exit(1)

    print("\n--- Bitcoin 24-Hour Price Report ---")
    print(f"Opening Price: ${metrics['opening_price']:.2f}")
    print(f"Closing Price: ${metrics['closing_price']:.2f}")
    print(f"24h High:      ${metrics['high_price']:.2f}")
    print(f"24h Low:       ${metrics['low_price']:.2f}")
    print(f"Change (%):    {metrics['percentage_change']:.2f}%")
    print("------------------------------------")

if __name__ == "__main__":
    main()