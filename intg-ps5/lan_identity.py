"""Sony DDP local console identification for DHCP-safe IP recovery."""
from __future__ import annotations

import socket
import time


def normalize_host_id(value: object) -> str:
    return str(value or "").strip().casefold()


def parse_ddp_fields(message: bytes) -> dict[str, str]:
    """Extract key:value fields from a DDP UDP reply without trusting names."""
    text = message.decode("utf-8", "replace")
    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        result[name.strip().casefold()] = value.strip()
    return result


def find_host_ip(host_id: str, *, timeout: float = 1.5) -> str | None:
    """Return an IPv4 address ONLY for the exact previously verified DDP host ID."""
    expected = normalize_host_id(host_id)
    if len(expected) < 8 or len(expected) > 128:
        return None
    message = b"SRCH * HTTP/1.1\r\ndevice-discovery-protocol-version:00030010\r\n\r\n"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(0.2)
        sock.sendto(message, ("255.255.255.255", 9302))
        deadline = time.monotonic() + max(0.2, min(timeout, 4.0))
        matches = set()
        while time.monotonic() < deadline:
            try:
                packet, peer = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if normalize_host_id(parse_ddp_fields(packet).get("host-id")) == expected:
                matches.add(peer[0])
        # Fail closed on ambiguity; never switch consoles based on display name.
        return next(iter(matches)) if len(matches) == 1 else None
