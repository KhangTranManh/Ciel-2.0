import requests

def get_crypto_prices_and_changes():
    """
    Retrieves the current price and 24-hour price change percentage for Bitcoin,
    Ethereum, and Solana from the CoinGecko public API and prints a formatted table.
    """
    coins = {
        "bitcoin": "Bitcoin",
        "ethereum": "Ethereum",
        "solana": "Solana"
    }
    vs_currency = "usd"
    api_url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": ",".join(coins.keys()),
        "vs_currencies": vs_currency,
        "include_24hr_change": "true"
    }

    try:
        response = requests.get(api_url, params=params)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from CoinGecko API: {e}")
        return
    except ValueError:
        print("Error: Could not decode JSON response from CoinGecko API.")
        return

    if not data:
        print("No data received from CoinGecko API.")
        return

    # Prepare data for table
    table_data = []
    for coin_id, coin_name in coins.items():
        if coin_id in data:
            price_usd = data[coin_id].get(vs_currency)
            change_24h = data[coin_id].get(f"{vs_currency}_24h_change")
            table_data.append({
                "Coin": coin_name,
                "Price (USD)": f"${price_usd:,.2f}" if price_usd is not None else "N/A",
                "24h Change (%)": f"{change_24h:+.2f}%" if change_24h is not None else "N/A"
            })
        else:
            table_data.append({
                "Coin": coin_name,
                "Price (USD)": "N/A",
                "24h Change (%)": "N/A"
            })

    # Print formatted table
    print(f"{'Coin':<12} {'Price (USD)':>15} {'24h Change (%)':>15}")
    print("-" * 42)
    for row in table_data:
        print(f"{row['Coin']:<12} {row['Price (USD)']:>15} {row['24h Change (%)']:>15}")

if __name__ == "__main__":
    get_crypto_prices_and_changes()