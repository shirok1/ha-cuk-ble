"""PIID 21 and PIID 7 protocol-switch bit helpers.

The u32 value packs per-port fast-charging protocol enable flags:

    c1: pd=bit0, pps=bit1, ufcs=bit2, reserved=bit3 (always 1)
    c2: pd=bit8, pps=bit9, ufcs=bit10, reserved=bit11 (always 1)
    c3: ufcs=bit16, scp=bit17
    a:  ufcs=bit24, scp=bit25

The reserved bit on C1/C2 is firmware-fixed to 1 and is re-asserted on writes
so they stay byte-identical to the Mi Home app.

Bit mappings confirmed in cuktech-ble-server ``state.py`` and aligned with
the Mi Home app's ``parseProtocolExtend`` / ``setProtocolExtend``.
"""

from __future__ import annotations

PROTOCOL_SWITCH_BITS: dict[str, dict[str, int]] = {
    "c1": {"pd": 0, "pps": 1, "ufcs": 2},
    "c2": {"pd": 8, "pps": 9, "ufcs": 10},
    "c3": {"ufcs": 16, "scp": 17},
    "a": {"ufcs": 24, "scp": 25},
}
_RESERVED_BITS = (1 << 3) | (1 << 11)


def protocol_switch_enabled(value: int, port: str, name: str) -> bool:
    """Return one named PIID 21 switch state."""
    return bool(value & (1 << _protocol_switch_bit(port, name)))


def update_protocol_switch(value: int, port: str, name: str, enabled: bool) -> int:
    """Return PIID 21 with one named switch flipped and reserved bits set."""
    bit = 1 << _protocol_switch_bit(port, name)
    return ((value | bit) if enabled else (value & ~bit)) | _RESERVED_BITS


def _protocol_switch_bit(port: str, name: str) -> int:
    try:
        return PROTOCOL_SWITCH_BITS[port][name]
    except KeyError:
        raise ValueError(f"port {port!r} does not have a {name!r} switch") from None


# ----- PIID 7 (protocol_ctrl) global gate bitmap ----------------------------
#
# Writable u8 with three flags (njcuk.fitting.ad1204.js parseProtocolInfo
# 5577-5604 and the DischargeProtocol page composer 27333-27403):
#
#     bit0 = SCP, bit1 = MiPPS (Xiaomi private PPS), bit2 = UFCS
#
# The UFCS gate applies to the C1/C2 ports only (i18n hint "only_c1c2").
# Upper bits are always zero — unlike PIID 21 there is no fixed reserved bit.
# The official per-toggle write flips one bit and keeps the others as read.

PROTOCOL_CTRL_BITS: dict[str, int] = {"scp": 0, "mipps": 1, "ufcs": 2}


def protocol_ctrl_enabled(value: int, name: str) -> bool:
    """Return one named PIID 7 switch state."""
    try:
        bit = PROTOCOL_CTRL_BITS[name]
    except KeyError:
        raise ValueError(f"unknown protocol gate {name!r}") from None
    return bool(value & (1 << bit))


def update_protocol_ctrl(value: int, name: str, enabled: bool) -> int:
    """Return ``value`` with one named flag flipped; unknown names raise."""
    try:
        bit = PROTOCOL_CTRL_BITS[name]
    except KeyError:
        raise ValueError(f"unknown protocol gate {name!r}") from None
    if enabled:
        return value | (1 << bit)
    return value & ~(1 << bit)
