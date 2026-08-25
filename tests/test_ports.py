import pytest

from cuktech_ble.ports import (
    PORT_CTL_BIT,
    PORT_PROPERTY_PIID,
    decode_pdo_caps,
    decode_port_info,
)


def test_decode_port_info_splits_bytes_into_voltage_current_protocol() -> None:
    # b0=01 in-use, b1=0a pd_fixed, b2=32 (5.0 A), b3=c8 (20.0 V) -> 100 W
    info = decode_port_info("c1", 0xC8320A01)

    assert info.port == "c1"
    assert info.raw_uint32_le == 0xC8320A01
    assert info.in_use is True
    assert info.protocol_code == 0x0A
    assert info.protocol_name == "pd_fixed"
    assert info.voltage_v == 20.0
    assert info.current_a == 5.0
    assert info.power_w == 100.0


def test_decode_port_info_idle_port_reports_zero_power() -> None:
    info = decode_port_info("a", 0x00_00_01_00)

    assert info.in_use is False
    assert info.protocol_name == "idle"
    assert info.power_w == 0.0


def test_decode_port_info_rejects_unknown_port_or_out_of_range_value() -> None:
    with pytest.raises(ValueError):
        decode_port_info("c4", 0)
    with pytest.raises(ValueError):
        decode_port_info("c1", -1)
    with pytest.raises(ValueError):
        decode_port_info("c1", 0x1_0000_0000)


def test_decode_pdo_caps_maps_watt_code_byte_to_known_wattage() -> None:
    # low half 0x0064 (100 W), high half 0x003c (60 W)
    caps = decode_pdo_caps(0x003C_0064, high_port="c1", low_port="c2")

    assert caps == {"c2": 100, "c1": 60}


def test_decode_pdo_caps_returns_none_for_zero_byte() -> None:
    # low half 0x0064 (100 W), high half 0x0000 (idle/no contract).
    caps = decode_pdo_caps(0x0000_0064, high_port="c3", low_port="a")

    assert caps == {"a": 100, "c3": None}


def test_port_ctl_bit_mapping_matches_reference_repos() -> None:
    """PIID 16 (port_ctl) bit index must be `piid - 1`.

    Reference: cuktech-ble-server ``1 << (piid - 1)`` and cuktech-ble-controller
    ``PORT_BITS = {'c1': 0, 'c2': 1, 'c3': 2, 'a': 3}``. A previous ha-cuk-ble
    write path used the raw piid as the bit index (off-by-one), so toggling a
    port flipped its neighbour and port "a" wrote bit 4 outside the 4-bit mask.
    """
    assert PORT_CTL_BIT == {"c1": 0, "c2": 1, "c3": 2, "a": 3}
    # bit_index == piid - 1 for every port — the invariant the coordinator's
    # write path must satisfy to stay consistent with the read path (is_on).
    for port, piid in PORT_PROPERTY_PIID.items():
        assert PORT_CTL_BIT[port] == piid - 1


def test_decode_negotiated_protocol_official_labels() -> None:
    """Kind bytes map to the plugin's getC1C2ProtocolStr labels."""
    from cuktech_ble.ports import NEGOTIATED_PROTOCOL_BY_KIND

    assert NEGOTIATED_PROTOCOL_BY_KIND == {
        0x01: "5v",
        0x02: "5v",
        0x03: "qc",
        0x04: "afc",
        0x05: "fcp",
        0x06: "scp",
        0x07: "pd",
        0x08: "pps",
        0x09: "pps",
        0x0A: "ufcs",
    }


def test_decode_negotiated_protocol_halves() -> None:
    from cuktech_ble.ports import decode_negotiated_protocol

    # C1 fixed PD 45W + C2 PPS 55W in one word (high16=C1, low16=C2).
    value = (0x07 << 24) | (45 << 16) | (0x08 << 8) | 55
    assert decode_negotiated_protocol(value, high_port="c1", low_port="c2") == {
        "c1": "pd",
        "c2": "pps",
    }

    # Zero low byte = no contract for that port.
    value = (0x0A << 24) | (100 << 16)
    assert decode_negotiated_protocol(value, high_port="c3", low_port="a") == {
        "c3": "ufcs",
        "a": None,
    }

    # Unknown kind byte → label None while the port still has a contract.
    value = (0x63 << 24) | (60 << 16)
    assert decode_negotiated_protocol(value, high_port="c1", low_port="c2") == {
        "c1": None,
        "c2": None,
    }
