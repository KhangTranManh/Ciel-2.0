from config import Config
from scraper import Scraper

if __name__ == "__main__":
    config = Config()
    scraper = Scraper(config)
    scraper.run()