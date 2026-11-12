"""URL normalization and allowlist validation utilities."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from copilot.rag.crawler.config import CrawlConfig

# Standard tracking and analytics parameters to strip during normalization
TRACKING_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "gclsrc",
    "dclid",
    "zanpid",
    "msclkid",
    "ref",
    "fref",
    "_hsenc",
    "_hsmi",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: str, *, strip_tracking: bool = True, strip_all_query: bool = False) -> str:
    """Normalize URL by stripping fragments, tracking parameters, and standardizing host/path.

    - Strips URL fragments (#...).
    - Standardizes scheme and host to lower case.
    - Strips standard default ports (:80 for http, :443 for https).
    - Normalizes duplicate slashes in the path.
    - Strips trailing slash on path components (except root '/').
    - Removes known tracking query parameters (or all query params if strip_all_query=True).
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    # Strip default ports from netloc
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    # Normalize path
    path = parts.path or "/"
    # Collapse multiple consecutive slashes
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    # Process query
    new_query = ""
    if parts.query and not strip_all_query:
        if strip_tracking:
            q_pairs = parse_qsl(parts.query, keep_blank_values=False)
            filtered = [
                (k, v)
                for k, v in q_pairs
                if k.lower() not in TRACKING_QUERY_PARAMS and not k.lower().startswith("utm_")
            ]
            new_query = urlencode(filtered)
        else:
            new_query = parts.query

    # urlunsplit: scheme, netloc, path, query, fragment
    return urlunsplit((scheme, netloc, path, new_query, ""))


def is_allowed_url(url: str, config: CrawlConfig) -> tuple[bool, str]:
    """Check whether a URL is permitted under the crawler configuration rules.

    Returns:
        tuple (is_allowed: bool, reason: str)
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return False, f"Disallowed scheme: '{scheme}' (must be http or https)"

    host = parts.hostname.lower() if parts.hostname else ""
    if not host:
        return False, "Missing hostname in URL"

    # Check host allowlist
    if config.allowed_hosts and host not in config.allowed_hosts:
        return False, f"Host '{host}' is not in allowed_hosts: {config.allowed_hosts}"

    # Check path prefixes
    path = parts.path or "/"
    if config.allowed_path_prefixes:
        prefix_matched = any(path.startswith(prefix) for prefix in config.allowed_path_prefixes)
        if not prefix_matched:
            return (
                False,
                f"Path '{path}' does not match any allowed prefix: {config.allowed_path_prefixes}",
            )

    # Check denied regex patterns against full url and path+query
    for pattern in config.denied_patterns:
        try:
            if re.search(pattern, url):
                return False, f"URL matches denied pattern: {pattern}"
        except re.error:
            # Fallback if pattern is literal or regex error
            if pattern in url:
                return False, f"URL matches denied pattern: {pattern}"

    return True, "Allowed"
