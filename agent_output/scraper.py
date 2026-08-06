import requests
from bs4 import BeautifulSoup

from agent_output.config import Config


class Scraper:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.headers)

    def fetch(self, url: str) -> str:
        response = self.session.get(
            self._build_url(url),
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.text

    def parse(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def scrape(self, url: str) -> BeautifulSoup:
        return self.parse(self.fetch(url))

    def _build_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        return f"{self.config.base_url.rstrip('/')}/{url.lstrip('/')}"