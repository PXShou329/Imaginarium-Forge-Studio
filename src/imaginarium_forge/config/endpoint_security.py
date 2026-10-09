"""Security-safe parsing for configured provider endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class EndpointSummary:
    """Security facts without retaining URL credentials or request components."""

    redacted_origin: str
    has_userinfo: bool
    uses_tls: bool
    valid: bool
    is_loopback: bool


def _loopback_host(host: str) -> bool:
    normalized = host.casefold().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def summarize_endpoint(url: str) -> EndpointSummary:
    """Return only scheme/host/port plus allow-listed validation facts.

    Base paths remain valid for reverse-proxied installations, but are omitted
    from display. User information, query strings and fragments are rejected
    and never retained in the returned summary.
    """

    raw = url.strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return EndpointSummary(
            redacted_origin="（無效端點）",
            has_userinfo=False,
            uses_tls=False,
            valid=False,
            is_loopback=False,
        )

    scheme = parsed.scheme.casefold()
    host = parsed.hostname or ""
    has_userinfo = parsed.username is not None or parsed.password is not None
    try:
        parsed_port = parsed.port
        port_is_valid = parsed_port is None or 1 <= parsed_port <= 65_535
    except ValueError:
        parsed_port = None
        port_is_valid = False
    authority_without_userinfo = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    if authority_without_userinfo.endswith(":"):
        port_is_valid = False

    display_scheme = scheme if scheme in {"http", "https"} else "unknown"
    display_host = host or "（無效主機）"
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    display_port = f":{parsed_port}" if parsed_port is not None else ""
    origin = f"{display_scheme}://{display_host}{display_port}"

    host_is_well_formed = bool(host) and not any(
        character.isspace() or character in {"/", "\\", "@", "#", "?"}
        for character in host
    )
    valid = (
        bool(raw)
        and scheme in {"http", "https"}
        and host_is_well_formed
        and port_is_valid
        and not has_userinfo
        and not parsed.query
        and not parsed.fragment
    )
    return EndpointSummary(
        redacted_origin=origin,
        has_userinfo=has_userinfo,
        uses_tls=scheme == "https",
        valid=valid,
        is_loopback=valid and _loopback_host(host),
    )


__all__ = ["EndpointSummary", "summarize_endpoint"]
