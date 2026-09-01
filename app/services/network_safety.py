"""Shared outbound-network safety checks for explicitly configured HTTPS adapters."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


def host_is_private(host: str) -> bool:
    """Return True when DNS resolves to a loopback/private/link-local/reserved address."""
    if host.casefold() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Resolution failures are handled by the HTTP client. Treat them as non-private here
        # rather than silently permitting a known-private target.
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            return True
    return False


def validate_https_endpoint(url: str, *, allow_private_network: bool = False, label: str = "Endpoint") -> str:
    """Validate an HTTPS URL and block private/loopback targets unless explicitly permitted."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError(f"{label} must be an HTTPS URL without embedded credentials.")
    if not allow_private_network and host_is_private(parts.hostname):
        raise ValueError(f"{label} resolves to a private/loopback address and is blocked by default.")
    return url.strip()
