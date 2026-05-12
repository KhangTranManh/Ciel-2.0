import requests

def get_bitcoin_price():
    """
    Fetches the current price of Bitcoin in USD from the CoinGecko API.
    Returns the price as a float, or an error message string if unsuccessful.
    """
    api_url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    try:
        response = requests.get(api_url)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        
        # Extract the price from the JSON response
        price = data.get('bitcoin', {}).get('usd')
        
        if price is None:
            return "Could not find Bitcoin price in API response."
        return float(price)
    except requests.exceptions.RequestException as e:
        return f"Error fetching data from API: {e}"
    except ValueError:
        return "Could not convert price to a number."
    except Exception as e:
        return f"An unexpected error occurred: {e}"

def print_markdown_table(crypto_name, price):
    """
    Prints the cryptocurrency name and its price formatted as a markdown table.
    """
    print("| Cryptocurrency | Price (USD) |")
    print("|----------------|-------------|")
    print(f"| {crypto_name}      | ${price:,.2f}   |")

if __name__ == "__main__":
    bitcoin_price = get_bitcoin_price()

    if isinstance(bitcoin_price, (float, int)):
        print_markdown_table("Bitcoin", bitcoin_price)
    else:
        print(f"Failed to retrieve Bitcoin price: {bitcoin_price}")
        print("Please ensure you have the 'requests' library installed (`pip install requests`).")