import re
from urllib.parse import urlparse
from typing import Tuple, Optional
import httpx

from app.core.http import create_async_http_client
from app.models.resource import AvailabilityStatus


URL_REGEX = re.compile(
    r"^https?://"  # http:// or https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain
    r"localhost|"  # localhost
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def validate_url_format(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return bool(URL_REGEX.match(url))


async def check_url_availability(
    url: str, timeout_seconds: float = 5.0
) -> Tuple[AvailabilityStatus, Optional[int]]:
    if not validate_url_format(url):
        return AvailabilityStatus.BROKEN, None

    headers = {
        "User-Agent": "ATC-DSA-Resource-Bot/1.0 (+https://atc.bennett.edu.in)"
    }
    try:
        async with create_async_http_client(
            follow_redirects=True, timeout=timeout_seconds, headers=headers
        ) as client:
            # First try HEAD request
            try:
                response = await client.head(url)
                # Some servers return 405 Method Not Allowed on HEAD
                if response.status_code == 405:
                    response = await client.get(url)
            except httpx.HTTPError:
                response = await client.get(url)

            code = response.status_code
            if 200 <= code < 400:
                return AvailabilityStatus.AVAILABLE, code
            elif 400 <= code < 500:
                return AvailabilityStatus.BROKEN, code
            else:
                return AvailabilityStatus.DEGRADED, code
    except (httpx.ConnectTimeout, httpx.ReadTimeout):
        return AvailabilityStatus.DEGRADED, None
    except httpx.HTTPError:
        return AvailabilityStatus.BROKEN, None
    except Exception:
        return AvailabilityStatus.BROKEN, None
