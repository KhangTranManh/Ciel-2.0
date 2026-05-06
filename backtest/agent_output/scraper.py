from config import Config
import requests
from bs4 import BeautifulSoup

class Scraper:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        # Potentially set user-agent or other headers from config
        if self.config.user_agent:
            self.session.headers.update({'User-Agent': self.config.user_agent})

    def fetch_html(self, url: str) -> str | None:
        """
        Fetches HTML content from a given URL.
        """
        try:
            response = self.session.get(url, timeout=self.config.request_timeout)
            response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
            return response.text
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {url}: {e}")
            return None

    def parse_html(self, html_content: str, selector: str) -> list[str]:
        """
        Parses HTML content using a CSS selector and returns a list of extracted text.
        """
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        elements = soup.select(selector)
        return [element.get_text(strip=True) for element in elements]

    def parse_html_attributes(self, html_content: str, selector: str, attribute: str) -> list[str]:
        """
        Parses HTML content using a CSS selector and returns a list of extracted attribute values.
        """
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        elements = soup.select(selector)
        return [element.get(attribute) for element in elements if element.get(attribute)]

# Example usage (for testing purposes, not part of the class itself)
if __name__ == "__main__":
    # Create a dummy Config class for testing
    class DummyConfig:
        def __init__(self):
            self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            self.request_timeout = 10

    # Instantiate the dummy config
    config = DummyConfig()

    # Instantiate the Scraper
    scraper = Scraper(config)

    # Example: Fetching and parsing a simple page
    test_url = "http://quotes.toscrape.com/"
    print(f"Fetching HTML from: {test_url}")
    html = scraper.fetch_html(test_url)

    if html:
        print("\n--- Parsing Quotes ---")
        quotes = scraper.parse_html(html, 'div.quote span.text')
        for i, quote in enumerate(quotes[:5]): # Print first 5 quotes
            print(f"Quote {i+1}: {quote}")

        print("\n--- Parsing Authors ---")
        authors = scraper.parse_html(html, 'div.quote small.author')
        for i, author in enumerate(authors[:5]): # Print first 5 authors
            print(f"Author {i+1}: {author}")

        print("\n--- Parsing Top Tags (text) ---")
        top_tags_text = scraper.parse_html(html, 'div.tags-box span.tag a.tag')
        print(f"Top Tags (text): {top_tags_text}")

        print("\n--- Parsing Top Tags (href attributes) ---")
        top_tags_hrefs = scraper.parse_html_attributes(html, 'div.tags-box span.tag a.tag', 'href')
        print(f"Top Tags (hrefs): {top_tags_hrefs}")
    else:
        print("Failed to fetch HTML for example usage.")