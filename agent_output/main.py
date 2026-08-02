from agent_output.config import Config
from agent_output.scraper import Scraper


def main() -> None:
    config = Config(
        base_url="https://example.com",
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )
    scraper = Scraper(config)
    scraper.run()


if __name__ == "__main__":
    main()