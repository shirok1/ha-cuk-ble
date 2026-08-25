"""Tests for the PIID 21 protocol_ctl_extend encode/decode helpers."""

from cuktech_ble.protocol_switches import (
    PROTOCOL_SWITCH_BITS,
    encode_protocol_extend,
    parse_protocol_switches,
)


def test_parse_all_disabled() -> None:
    """A zero value means all protocol switches are off (except reserved bits)."""
    switches = parse_protocol_switches(0)
    assert switches == {
        "c1": {"pd": False, "pps": False, "ufcs": False},
        "c2": {"pd": False, "pps": False, "ufcs": False},
        "c3": {"ufcs": False, "scp": False},
        "a": {"ufcs": False, "scp": False},
    }


def test_parse_c1_all_enabled() -> None:
    """C1's PD/PPS/UFCS live in the low nibble (bits 0-2)."""
    # bits 0,1,2 set = 0x07; reserved bit 3 also set by firmware = 0x0F
    switches = parse_protocol_switches(0x0F)
    assert switches["c1"] == {"pd": True, "pps": True, "ufcs": True}


def test_parse_c2_all_enabled() -> None:
    """C2's PD/PPS/UFCS live in bits 8-10; reserved bit 11 = 0x0F00."""
    switches = parse_protocol_switches(0x0F00)
    assert switches["c2"] == {"pd": True, "pps": True, "ufcs": True}


def test_parse_c3_switches() -> None:
    """C3 has UFCS (bit16) and SCP (bit17)."""
    switches = parse_protocol_switches(0x00030000)
    assert switches["c3"] == {"ufcs": True, "scp": True}


def test_parse_a_switches() -> None:
    """A has UFCS (bit24) and SCP (bit25)."""
    switches = parse_protocol_switches(0x03000000)
    assert switches["a"] == {"ufcs": True, "scp": True}


def test_encode_empty_dict_produces_zero() -> None:
    """No switches provided → all zeros (reserved bits not asserted when nil)."""
    assert encode_protocol_extend({}) == 0


def test_encode_c1_all_round_trips_through_reserved_bit() -> None:
    """Encoding C1 PD/PPS/UFCS all-on should assert the reserved bit too."""
    value = encode_protocol_extend({
        "c1": {"pd": True, "pps": True, "ufcs": True},
    })
    # reserved bit (bit 3) is re-asserted by encode → low byte = 0x0F
    assert value == 0x0F
    switches = parse_protocol_switches(value)
    assert switches["c1"] == {"pd": True, "pps": True, "ufcs": True}


def test_encode_c1_c2_full_round_trip() -> None:
    """Full C1+C2 with reserved bits = 0x0F0F."""
    value = encode_protocol_extend({
        "c1": {"pd": True, "pps": True, "ufcs": True},
        "c2": {"pd": True, "pps": True, "ufcs": True},
    })
    assert value == 0x0F0F


def test_encode_decode_round_trip() -> None:
    """A representative mixed-state value round-trips correctly."""
    original = {
        "c1": {"pd": True, "pps": False, "ufcs": True},
        "c2": {"pd": False, "pps": True, "ufcs": False},
        "c3": {"ufcs": True, "scp": False},
        "a": {"ufcs": False, "scp": True},
    }
    value = encode_protocol_extend(original)
    decoded = parse_protocol_switches(value)
    assert decoded == original


def test_protocol_switch_bits_layout_matches_reference_server() -> None:
    """Bit positions must match cuktech-ble-server state.py PROTOCOL_SWITCH_BITS."""
    assert PROTOCOL_SWITCH_BITS == {
        "c1": {"pd": 0, "pps": 1, "ufcs": 2, "_reserved": 3},
        "c2": {"pd": 8, "pps": 9, "ufcs": 10, "_reserved": 11},
        "c3": {"ufcs": 16, "scp": 17},
        "a": {"ufcs": 24, "scp": 25},
    }


def test_encode_individual_c1_pd_toggle() -> None:
    """Toggling only C1 PD sets bit 0 (plus reserved bit 3 = 0x09)."""
    value = encode_protocol_extend({"c1": {"pd": True}})
    assert value == 0x09
