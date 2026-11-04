"""Send a provider request with Ciel's Python HTTP client; secrets stay on stdin."""

import json
import sys
from urllib.parse import urlsplit

import httpx


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    request = json.load(sys.stdin)
    try:
        local = urlsplit(request["endpoint"]).hostname in {"localhost", "127.0.0.1", "::1"}
        with httpx.Client(timeout=120.0, follow_redirects=False, trust_env=not local) as client:
            response = client.post(
                request["endpoint"],
                headers={"Authorization": "Bearer " + request["apiKey"]},
                json=request["body"],
            )
            if not response.is_success:
                print(json.dumps({"ok": False, "status": response.status_code}))
                return
            print(json.dumps({"ok": True, "data": response.json()}))
    except (httpx.HTTPError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "Could not connect to the AI provider. Check BASE_URL and network access.", "errorType": type(exc).__name__}))


if __name__ == "__main__":
    main()
