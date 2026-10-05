"""Firmware-version GATT helpers for the AD1204U."""

from __future__ import annotations

import re

FIRMWARE_VERSION_UUID = "00000004-0000-1000-8000-00805f9b34fb"


def decode_firmware_version(raw: bytes | bytearray | memoryview) -> str | None:
    """Decode the charger's NUL-padded ASCII firmware version characteristic."""
    text = bytes(raw).split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
    return text or None


def uses_port_protocols(version: str | None) -> bool | None:
    """Match Mi Home's strictly-newer-than-2.1.2_0040 capability boundary."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)_(\d+)", version or "")
    if match is None:
        return None
    return tuple(map(int, match.groups())) > (2, 1, 2, 40)
