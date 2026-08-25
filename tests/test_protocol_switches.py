"""Tests for the protocol switch bit helpers."""

import pytest

from cuktech_ble.protocol_switches import (
    PROTOCOL_SWITCH_BITS,
    protocol_ctrl_enabled,
    protocol_switch_enabled,
    update_protocol_ctrl,
    update_protocol_switch,
)


@pytest.mark.parametrize(
    ("value", "port", "name"),
    [
        (0x01, "c1", "pd"),
        (0x0200, "c2", "pps"),
        (0x00010000, "c3", "ufcs"),
        (0x02000000, "a", "scp"),
    ],
)
def test_protocol_switch_enabled(value: int, port: str, name: str) -> None:
    assert protocol_switch_enabled(value, port, name)
    assert not protocol_switch_enabled(0, port, name)


def test_protocol_switch_bits_match_reference_server() -> None:
    assert PROTOCOL_SWITCH_BITS == {
        "c1": {"pd": 0, "pps": 1, "ufcs": 2},
        "c2": {"pd": 8, "pps": 9, "ufcs": 10},
        "c3": {"ufcs": 16, "scp": 17},
        "a": {"ufcs": 24, "scp": 25},
    }


def test_update_protocol_switch_flips_one_bit_and_sets_reserved_bits() -> None:
    assert update_protocol_switch(0, "c1", "pd", True) == 0x0809
    assert update_protocol_switch(0xFFFF, "c1", "pd", False) == 0xFFFE


def test_protocol_switch_rejects_unknown_switch() -> None:
    with pytest.raises(ValueError):
        update_protocol_switch(0, "a", "pd", True)


def test_protocol_ctrl_helpers() -> None:
    assert protocol_ctrl_enabled(3, "scp")
    assert protocol_ctrl_enabled(3, "mipps")
    assert not protocol_ctrl_enabled(3, "ufcs")
    assert update_protocol_ctrl(3, "ufcs", True) == 7
    assert update_protocol_ctrl(7, "ufcs", False) == 3


def test_protocol_ctrl_rejects_unknown_gate() -> None:
    with pytest.raises(ValueError):
        protocol_ctrl_enabled(7, "pd")
    with pytest.raises(ValueError):
        update_protocol_ctrl(7, "pd", True)
