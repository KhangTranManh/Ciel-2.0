from config import Config
import requests
from bs4 import BeautifulSoup

class Scraper:
    def __init__(self, config: Config):
        self.config = config

    def fetch_html(self, url: str) -> str:
        response = requests.get(
            url,
            headers=self.config.headers,
            timeout=self.config.timeout
        )
        response.raise_for_status()
        return response.text

    def parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, 'html.parser')

    def scrape(self, url: str) -> BeautifulSoup:
        html = self.fetch_html(url)
        return self.parse_html(html)