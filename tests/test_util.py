import stat

from cuktech_ble.util import write_private_text


def test_write_private_text_replaces_permissive_file(tmp_path) -> None:
    output = tmp_path / "token.json"
    output.write_text("old secret")
    output.chmod(0o644)

    write_private_text(output, "new secret")

    assert output.read_text() == "new secret"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [output]
