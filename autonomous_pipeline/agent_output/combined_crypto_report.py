import requests
import os

def get_btc_stats(filename='btc_24h_summary.txt'):
    """
    Reads BTC's 24h high, low, and percentage change from a local text file.
    Assumes the file contains lines like:
    "High: $70,500.00"
    "Low: $68,123.45"
    "24h Change: +1.75%"
    """
    btc_high = None
    btc_low = None
    btc_change = None

    if not os.path.exists(filename):
        print(f"Error: BTC summary file '{filename}' not found.")
        return None, None, None

    try:
        with open(filename, 'r') as f:
            for line in f:
                if "High:" in line:
                    # Extract number, remove '$', ',', and convert to float
                    btc_high = float(line.split(':')[1].strip().replace('$', '').replace(',', ''))
                elif "Low:" in line:
                    # Extract number, remove '$', ',', and convert to float
                    btc_low = float(line.split(':')[1].strip().replace('$', '').replace(',', ''))
                elif "24h Change:" in line:
                    # Extract number, remove '%', and convert to float
                    btc_change = float(line.split(':')[1].strip().replace('%', ''))
        return btc_high, btc_low, btc_change
    except Exception as e:
        print(f"Error parsing BTC summary file '{filename}': {e}")
        return None, None, None

def get_crypto_api_stats(coin_ids, vs_currency='usd'):
    """
    Fetches 24h high, low, and percentage change for specified cryptocurrencies
    from the CoinGecko API.
    """
    base_url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        'ids': ','.join(coin_ids),
        'vs_currencies': vs_currency,
        'include_24hr_high_low': 'true',
        'include_24hr_change': 'true'
    }
    headers = {
        'Accept': 'application/json'
    }

    results = {}
    for coin_id in coin_ids:
        results[coin_id] = {'high': None, 'low': None, 'change': None} # Initialize with None

    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=10)
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        for coin_id in coin_ids:
            if coin_id in data:
                coin_data = data[coin_id]
                high = coin_data.get(f'{vs_currency}_24h_high')
                low = coin_data.get(f'{vs_currency}_24h_low')
                change = coin_data.get(f'{vs_currency}_24h_change')
                results[coin_id] = {'high': high, 'low': low, 'change': change}
            else:
                print(f"Warning: Data for '{coin_id}' not found in API response.")
        return results
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from CoinGecko API: {e}")
        return results # Return initialized results with None values
    except ValueError as e:
        print(f"Error parsing JSON response from CoinGecko API: {e}")
        return results # Return initialized results with None values

def main():
    # --- Step 1: Get BTC stats from local file ---
    btc_high, btc_low, btc_change = get_btc_stats('btc_24h_summary.txt')

    # --- Step 2: Get ETH and SOL stats from public API ---
    # CoinGecko uses 'ethereum' and 'solana' as IDs
    api_coins_to_fetch = ['ethereum', 'solana']
    api_stats = get_crypto_api_stats(api_coins_to_fetch)

    eth_stats = api_stats.get('ethereum', {'high': None, 'low': None, 'change': None})
    sol_stats = api_stats.get('solana', {'high': None, 'low': None, 'change': None})

    # --- Prepare data for the report ---
    report_data = []

    # BTC Data
    report_data.append({
        'Asset': 'BTC',
        'High': f"${btc_high:,.2f}" if btc_high is not None else 'N/A',
        'Low': f"${btc_low:,.2f}" if btc_low is not None else 'N/A',
        '24h Change': f"{btc_change:+.2f}%" if btc_change is not None else 'N/A'
    })

    # ETH Data
    report_data.append({
        'Asset': 'ETH',
        'High': f"${eth_stats['high']:,.2f}" if eth_stats['high'] is not None else 'N/A',
        'Low': f"${eth_stats['low']:,.2f}" if eth_stats['low'] is not None else 'N/A',
        '24h Change': f"{eth_stats['change']:+.2f}%" if eth_stats['change'] is not None else 'N/A'
    })

    # SOL Data
    report_data.append({
        'Asset': 'SOL',
        'High': f"${sol_stats['high']:,.2f}" if sol_stats['high'] is not None else 'N/A',
        'Low': f"${sol_stats['low']:,.2f}" if sol_stats['low'] is not None else 'N/A',
        '24h Change': f"{sol_stats['change']:+.2f}%" if sol_stats['change'] is not None else 'N/A'
    })

    # --- Step 3: Write combined market summary report to 'crypto_24h_combined.txt' ---
    output_filename = 'crypto_24h_combined.txt'
    with open(output_filename, 'w') as f:
        f.write("Crypto Market 24h Summary Report\n")
        f.write("--------------------------------\n\n")

        # Determine column widths for dynamic formatting
        asset_width = max(len(d['Asset']) for d in report_data)
        high_width = max(len(d['High']) for d in report_data)
        low_width = max(len(d['Low']) for d in report_data)
        change_width = max(len(d['24h Change']) for d in report_data)

        # Headers
        header = (f"{'Asset':<{asset_width}} | "
                  f"{'High':>{high_width}} | "
                  f"{'Low':>{low_width}} | "
                  f"{'24h Change':>{change_width}}")
        f.write(header + '\n')
        f.write('-' * len(header) + '\n')

        # Data rows
        for item in report_data:
            line = (f"{item['Asset']:<{asset_width}} | "
                    f"{item['High']:>{high_width}} | "
                    f"{item['Low']:>{low_width}} | "
                    f"{item['24h Change']:>{change_width}}")
            f.write(line + '\n')

    print(f"Market summary report successfully written to '{output_filename}'")

if __name__ == "__main__":
    # Create a dummy 'btc_24h_summary.txt' if it doesn't exist for demonstration purposes.
    # In a real scenario, this file would be provided or generated by another process.
    if not os.path.exists('btc_24h_summary.txt'):
        print("Creating dummy 'btc_24h_summary.txt' for demonstration.")
        with open('btc_24h_summary.txt', 'w') as f:
            f.write("BTC 24h Summary:\n")
            f.write("High: $70,500.00\n")
            f.write("Low: $68,123.45\n")
            f.write("24h Change: +1.75%\n")
        print("Please ensure 'requests' library is installed (pip install requests).")

    main()