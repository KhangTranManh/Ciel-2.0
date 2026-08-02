from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from agent_output.config import Config


class Scraper:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.headers)

    def fetch(self, url: str = "") -> str:
        target_url = urljoin(self.config.base_url.rstrip("/") + "/", url.lstrip("/"))
        response = self.session.get(target_url, timeout=self.config.timeout)
        response.raise_for_status()
        return response.text

    def parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def scrape(self, url: str = "") -> BeautifulSoup:
        return self.parse_html(self.fetch(url))