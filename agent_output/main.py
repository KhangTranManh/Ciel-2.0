from config import Config
from scraper import Scraper

def main():
    config = Config()
    scraper = Scraper(config)
    scraper.run()

if __name__ == "__main__":
    main()