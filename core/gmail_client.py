"""Small compatibility boundary for constructing the Gmail API client.

LangChain deprecated ``build_resource_service`` in favor of the Gmail-specific
``build_gmail_service``. Keep the fallback here so the rest of Ciel uses the new
API without making an older installed dependency an immediate startup break.
"""
from __future__ import annotations

from typing import Any


def build_gmail_api_service(*, credentials: Any, utils_module=None):
    """Build a Gmail service, preferring the current LangChain helper.

    The import stays lazy because Gmail is optional. Callers that never enable or
    load Gmail should not need the Google community package at import time.
    """
    if utils_module is None:
        from langchain_google_community.gmail import utils as utils_module

    builder = getattr(utils_module, "build_gmail_service", None)
    if builder is None:
        builder = getattr(utils_module, "build_resource_service", None)
    if builder is None:
        raise RuntimeError("gmail_service_builder_unavailable")
    return builder(credentials=credentials)


def close_gmail_api_service(service: Any) -> None:
    """Close a transient Google API transport when the installed client supports it."""
    close = getattr(service, "close", None)
    if callable(close):
        close()
