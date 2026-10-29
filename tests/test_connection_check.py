"""The bench check must fail on auth/read errors and always release BLE."""

import asyncio
from types import SimpleNamespace

import pytest

from tools import ad1204u_connection_check as check


@pytest.mark.parametrize("failure", [None, "login", "read"])
def test_cycle_verdict_and_cleanup(monkeypatch, capsys, failure):
    calls = []

    class Client:
        mtu_size = 247

        def __init__(self, *args, **kwargs):
            pass

        async def connect(self):
            calls.append("connect")

        async def disconnect(self):
            calls.append("disconnect")

    class Auth:
        def __init__(self, *args, **kwargs):
            pass

        async def subscribe(self, **kwargs):
            pass

        async def greet(self):
            calls.append("greet")

        async def subscribe_upnp(self):
            pass

        async def login(self, token):
            calls.append("login")
            if failure == "login":
                raise RuntimeError("sensitive packet must not be printed")
            return object()

        async def unsubscribe(self):
            calls.append("auth_cleanup")

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def subscribe(self):
            pass

        async def initialize(self):
            pass

        async def unsubscribe(self):
            calls.append("session_cleanup")

    async def read(*args):
        calls.append("read")
        if failure == "read":
            return {}
        return {key: SimpleNamespace(status=0) for key in range(4)}

    async def sleep(delay):
        calls.append(delay)

    monkeypatch.setattr(check, "BleakClient", Client)
    monkeypatch.setattr(check, "MiAuthClient", Auth)
    monkeypatch.setattr(check, "MiSession", Session)
    monkeypatch.setattr(check, "get_properties", read)
    monkeypatch.setattr(check.asyncio, "sleep", sleep)
    result = asyncio.run(check.cycle(object(), b"x" * 12,
                                    delay=0.1, acquire_mtu=False))
    assert result is (failure is None)
    assert calls[calls.index("greet") + 1] == 0.1
    assert calls[-2:] == ["auth_cleanup", "disconnect"]
    if failure != "login":
        assert calls[-3] == "session_cleanup"
    output = capsys.readouterr().out
    assert "sensitive" not in output
    if failure is not None:
        assert f'"stage": "{failure}"' in output
