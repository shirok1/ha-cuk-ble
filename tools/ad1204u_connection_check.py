"""Repeat read-only connect/login/read/disconnect cycles, emitting safe JSONL.

--greeting-delay adds an experimental delay after the production A4 settling.
Stop other clients (including this device's HA entry) before running.
No pairing, reset, or property-setting commands are sent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner

from cuktech_ble.xiaomi.auth import MiAuthClient
from cuktech_ble.xiaomi.properties import get_properties
from cuktech_ble.xiaomi.session import MiSession


def report(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


async def cycle(device, token: bytes, *, delay: float, acquire_mtu: bool) -> bool:
    """Use production auth/session code; report only stages, not raw packets."""
    client = BleakClient(device, timeout=20)
    auth = MiAuthClient(client, timeout=10)
    session = None
    stage = "connect"
    started = time.monotonic()
    passed = False
    try:
        await client.connect()
        stage = "mtu"
        backend = getattr(client, "_backend", None)
        if acquire_mtu and backend is not None and hasattr(backend, "_acquire_mtu"):
            await backend._acquire_mtu()
        report("connected", mtu=client.mtu_size)
        stage = "greeting"
        await auth.subscribe(upnp=False)
        await auth.greet()
        await asyncio.sleep(delay)
        stage = "login"
        await auth.subscribe_upnp()
        keys = await auth.login(token)
        stage = "initialize"
        session = MiSession(auth, keys, timeout=10)
        await session.subscribe()
        await session.initialize()
        await asyncio.sleep(0.3)
        stage = "read"
        for _ in range(3):
            values = await get_properties(session, ((2, 1), (2, 2), (2, 3), (2, 4)))
            if len(values) != 4 or any(item.status != 0 for item in values.values()):
                raise ValueError("missing or rejected port properties")
            await asyncio.sleep(0.2)
        passed = True
    except Exception as exc:
        report("failed", stage=stage, error=type(exc).__name__)
    finally:
        for resource, method in ((session, "unsubscribe"), (auth, "unsubscribe"),
                                 (client, "disconnect")):
            if resource is not None:
                try:
                    await getattr(resource, method)()
                except Exception as exc:
                    passed = False
                    report("failed", stage="cleanup", error=type(exc).__name__)
        report("cycle_finished", passed=passed,
               seconds=round(time.monotonic() - started, 3))
    return passed


async def run(args: argparse.Namespace) -> int:
    credentials = json.loads(args.token_file.read_text())
    token = bytes.fromhex(credentials["token_hex"])
    if len(token) != 12:
        raise ValueError("expected a 12-byte BLE token")
    address = args.address or credentials["address"]
    passed = 0
    for index in range(args.cycles):
        report("cycle_started", cycle=index + 1, greeting_delay=args.greeting_delay,
               acquire_mtu=not args.skip_acquire_mtu)
        device = await BleakScanner.find_device_by_address(address, timeout=20)
        if device is None:
            report("failed", stage="scan", error="NotAdvertising")
        else:
            passed += await cycle(device, token, delay=args.greeting_delay,
                                  acquire_mtu=not args.skip_acquire_mtu)
        if index + 1 < args.cycles:
            await asyncio.sleep(args.interval)
    report("summary", passed=passed, total=args.cycles)
    return 0 if passed == args.cycles else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path,
                        default=Path.home() / ".cuktech_ble.token")
    parser.add_argument("--address")
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--greeting-delay", type=float, default=0)
    parser.add_argument("--skip-acquire-mtu", action="store_true")
    args = parser.parse_args()
    if args.cycles < 1 or args.interval < 0 or args.greeting_delay < 0:
        parser.error("cycles must be positive and delays must be nonnegative")
    # Auth errors can include packet bytes; the JSONL above is deliberately
    # limited to exception types and stages, with no credentials or payloads.
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        report("failed", stage="setup", error=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
