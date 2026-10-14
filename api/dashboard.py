"""Serve the Flutter web build (the dashboard) from the API process.

One origin for the page, REST and WebSocket means no CORS, one Cloudflare hostname,
and the app can default to "this server". The build is public static files (it holds
no secrets); every API route stays behind the access token.
"""
from __future__ import annotations

import os
from pathlib import Path

from starlette.routing import Match, Mount
from starlette.staticfiles import StaticFiles

DASHBOARD_ROUTE_NAME = "dashboard"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def find_web_dir() -> Path | None:
    """CIEL_WEB_DIR wins; otherwise the image path, then a local `flutter build web`."""
    candidates = [os.getenv("CIEL_WEB_DIR", "").strip(),
                  str(_REPO_ROOT / "web"),
                  str(_REPO_ROOT / "ciel_app" / "build" / "web")]
    for candidate in candidates:
        if candidate and (Path(candidate) / "index.html").is_file():
            return Path(candidate)
    return None


class _NoCacheStaticFiles(StaticFiles):
    """Browsers revalidate every file (cheap 304s via ETag), so a deploy is picked up
    on the next page load instead of serving a stale app against a newer API."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def mount_dashboard(app, web_dir: Path | None) -> bool:
    """Mount at "/" — call after every API route is registered so none is shadowed."""
    if web_dir is None:
        return False
    app.mount("/", _NoCacheStaticFiles(directory=str(web_dir), html=True), name=DASHBOARD_ROUTE_NAME)
    return True


def is_dashboard_request(app, scope) -> bool:
    """True when the first fully matching route is the dashboard mount, i.e. the path
    is not an API route. Deciding by routing (not a path list) keeps any route added
    later protected by default."""
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return isinstance(route, Mount) and route.name == DASHBOARD_ROUTE_NAME
    return False
