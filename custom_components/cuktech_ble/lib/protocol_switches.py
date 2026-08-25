"""PIID 21 (protocol_ctl_extend) bit layout and encode/decode helpers.

The u32 value packs per-port fast-charging protocol enable flags:

    c1: pd=bit0, pps=bit1, ufcs=bit2, reserved=bit3 (always 1)
    c2: pd=bit8, pps=bit9, ufcs=bit10, reserved=bit11 (always 1)
    c3: ufcs=bit16, scp=bit17
    a:  ufcs=bit24, scp=bit25

The reserved bit on C1/C2 is firmware-fixed to 1; ``encode_protocol_extend``
always re-asserts it so writes stay byte-identical to the Mi Home app.

Bit mappings confirmed in cuktech-ble-server ``state.py`` and aligned with
the Mi Home app's ``parseProtocolExtend`` / ``setProtocolExtend``.
"""

from __future__ import annotations

PROTOCOL_SWITCH_BITS: dict[str, dict[str, int]] = {
    "c1": {"pd": 0, "pps": 1, "ufcs": 2, "_reserved": 3},
    "c2": {"pd": 8, "pps": 9, "ufcs": 10, "_reserved": 11},
    "c3": {"ufcs": 16, "scp": 17},
    "a": {"ufcs": 24, "scp": 25},
}


def parse_protocol_switches(value: int) -> dict[str, dict[str, bool]]:
    """Decode a PIID 21 u32 into per-port switch states."""
    result: dict[str, dict[str, bool]] = {}
    for port, bits in PROTOCOL_SWITCH_BITS.items():
        port_switches: dict[str, bool] = {}
        for name, bit in bits.items():
            if name == "_reserved":
                continue
            port_switches[name] = bool(value & (1 << bit))
        result[port] = port_switches
    return result


def _c1c2_flags(switches: dict[str, bool] | None) -> int:
    """Encode the 4-bit flag word for C1 or C2 (reserved bit fixed to 1)."""
    if not switches:
        return 0
    v = 0x08  # reserved bit always 1
    if switches.get("pd"):
        v |= 0x01
    if switches.get("pps"):
        v |= 0x02
    if switches.get("ufcs"):
        v |= 0x04
    return v


def _c3a_flags(switches: dict[str, bool] | None) -> int:
    """Encode the 2-bit flag word for C3 or A."""
    if not switches:
        return 0
    v = 0
    if switches.get("ufcs"):
        v |= 0x01
    if switches.get("scp"):
        v |= 0x02
    return v


def encode_protocol_extend(switches: dict[str, dict[str, bool]]) -> int:
    """Encode per-port switch states into a PIID 21 u32 value."""
    c1 = _c1c2_flags(switches.get("c1"))
    c2 = _c1c2_flags(switches.get("c2"))
    c3 = _c3a_flags(switches.get("c3"))
    a = _c3a_flags(switches.get("a"))
    return (a << 24) | (c3 << 16) | (c2 << 8) | c1


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


def parse_protocol_ctrl(value: int) -> dict[str, bool]:
    """Decode a PIID 7 u8 into named flag states."""
    return {name: bool(value & (1 << bit)) for name, bit in PROTOCOL_CTRL_BITS.items()}


def update_protocol_ctrl(value: int, name: str, enabled: bool) -> int:
    """Return ``value`` with one named flag flipped; unknown names raise."""
    try:
        bit = PROTOCOL_CTRL_BITS[name]
    except KeyError:
        raise ValueError(f"unknown protocol gate {name!r}") from None
    if enabled:
        return value | (1 << bit)
    return value & ~(1 << bit)
