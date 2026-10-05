from cuktech_ble.firmware import FIRMWARE_VERSION_UUID, decode_firmware_version


def test_firmware_uuid_matches_mi_home_capture() -> None:
    assert FIRMWARE_VERSION_UUID == "00000004-0000-1000-8000-00805f9b34fb"


def test_decode_firmware_version_strips_nul_padding() -> None:
    raw = bytes.fromhex("32 2e 31 2e 32 5f 30 30 37 33 00 00 00 00 00 00 00 00 00 00")

    assert decode_firmware_version(raw) == "2.1.2_0073"


def test_decode_firmware_version_returns_none_for_empty_value() -> None:
    assert decode_firmware_version(b"\x00" * 20) is None


def test_protocol_capability_boundary_and_unknown_firmware() -> None:
    from cuktech_ble.firmware import uses_port_protocols

    assert uses_port_protocols("2.1.2_0039") is False
    assert uses_port_protocols("2.1.2_0040") is False
    assert uses_port_protocols("2.1.2_0041") is True
    assert uses_port_protocols("2.1.2_0073") is True
    assert uses_port_protocols("2.1.10_0001") is True
    assert uses_port_protocols(None) is None
    assert uses_port_protocols("unknown") is None
