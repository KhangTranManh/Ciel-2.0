import datetime

def print_current_date_formatted():
    """
    Prints the current date in 'YYYY-MM-DD' format.
    """
    today = datetime.date.today()
    print(today.strftime('%Y-%m-%d'))

if __name__ == "__main__":
    print_current_date_formatted()