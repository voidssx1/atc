"""Cloudflare Python Workers root entrypoint exporting `Default` from `app.main`."""

from app.main import Default, app

__all__ = ["Default", "app"]
