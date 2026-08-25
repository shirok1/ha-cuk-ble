"""DataUpdateCoordinator for the CUKTECH AD1204U charger.

Strategy: push-driven BLE session with periodic reconciliation and an idle lease.

- On first refresh, connect + login + enter the MIOT application session.
- ACK and merge spontaneous property events as soon as they arrive.
- Reconcile all siid=2 properties every ``update_interval`` seconds when pushes
  are quiet.
- Release the connection ``idle_release`` seconds after login or the last explicit
  property write. Scheduled polls do not extend the lease; the next poll reconnects.

Writes follow the official Mi Home plugin's feedback model:

- The set-properties response is the authoritative confirmation; a response
  timeout is retried once after a short pause (the plugin resends on -3).
- The confirmed value is merged optimistically and published immediately, so
  entities flip as soon as the charger accepts a write — no blocking re-read.
- The charger echoes every setting change as a property-changed push; those
  pushes keep the state honest afterwards.
- One debounced single-property re-read ~3 s later reconciles a missed echo.
  It never blocks the caller and its failure never fails the write or drops
  the connection.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, AsyncIterator

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    WRITE_CONFIRM_TIMEOUT,
    WRITE_RECONCILE_DELAY,
    WRITE_RETRY_DELAY,
)
from .lib.firmware import FIRMWARE_VERSION_UUID, decode_firmware_version
from .lib.ports import (
    PIID_TO_PORT,
    PORTS,
    PortInfo,
    decode_pdo_caps,
    decode_port_info,
)
from .lib.xiaomi import MiAuthClient
from .lib.xiaomi.auth import MiAuthInvalidTokenError
from .lib.xiaomi.properties import (
    DEFAULT_READ_TUPLES,
    MiotProtocolError,
    PropertyValue,
    do_action,
    get_properties,
    parse_notification,
    set_property,
)
from .lib.xiaomi.session import MiSession, MiSessionTimeout

_LOGGER = logging.getLogger(__name__)

# reset_setting action: restore charger settings to factory defaults
# (njcuk.fitting.ad1204.js service map, aiid block).
FACTORY_RESET_SIID = 2
FACTORY_RESET_AIID = 1


@dataclass
class AD1204UData:
    """Snapshot exposed to entities."""

    ports: dict[str, PortInfo] = field(default_factory=dict)
    total_power_w: float = 0.0
    pdo_caps_w: dict[str, int | None] = field(default_factory=dict)
    scene_mode: int | None = None
    screen_save_time: int | None = None
    device_language: int | None = None
    port_ctl: int | None = None
    protocol_ctl: int | None = None
    protocol_ctl_extend: int | None = None
    usb_a_always_on: bool | None = None
    screenoff_while_idle: bool | None = None
    screen_dir_lock: bool | None = None


class AD1204UCoordinator(DataUpdateCoordinator[AD1204UData]):
    """Own the live BLE+MIOT session, push updates, and reconciliation reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry[Any],
        address: str,
        token: bytes,
        name: str,
        update_interval: float,
        idle_release: float,
        connection_timeout: float,
        bluez_start_notify: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"ad1204u {address}",
            update_interval=timedelta(seconds=update_interval),
        )
        self.address = address
        self.device_name = name
        self._token = token
        self._idle_release = idle_release
        self._connection_timeout = connection_timeout
        self._bluez_start_notify = bluez_start_notify

        self._client: BleakClient | None = None
        self._connecting_client: BleakClient | None = None
        self._auth: MiAuthClient | None = None
        self._session: MiSession | None = None
        self._connection_generation = 0
        self.firmware_version: str | None = None
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._reconcile_task: asyncio.Task[None] | None = None
        self._reconcile_keys: set[tuple[int, int]] = set()
        self._property_values: dict[tuple[int, int], PropertyValue] = {}
        self._pending_notification_values: dict[tuple[int, int], PropertyValue] = {}
        self._baseline_session: MiSession | None = None
        self._notification_publish_pending = False

    # ------------------------------------------------------------ lifecycle
    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected disconnects."""
        self.hass.loop.call_soon_threadsafe(self._handle_disconnect, client)

    def _handle_disconnect(self, client: BleakClient) -> None:
        """Mark unavailable and clean up a disconnected session on the HA loop."""
        if self._connecting_client is client:
            # _ensure_connected() owns cleanup until the setup is published as
            # the current connection. Invalidating its generation prevents a
            # late successful login from installing this disconnected client.
            self._connection_generation += 1
            _LOGGER.debug("AD1204U %s disconnected during setup", self.address)
            return
        if self._client is not client:
            return
        _LOGGER.debug("AD1204U %s disconnected unexpectedly", self.address)
        self._connection_generation += 1
        # Invalidate push publication before marking the coordinator failed.
        # A notification already queued on the event loop must not make stale
        # data available again after this disconnect.
        self._baseline_session = None
        self._notification_publish_pending = False
        self.async_set_update_error(BleakError("Device disconnected unexpectedly"))
        if self._disconnect_task is not None and not self._disconnect_task.done():
            return
        task = self.hass.async_create_background_task(
            self._cleanup_disconnected_client(client),
            name=f"ad1204u_disconnect_{self.address}",
        )
        self._disconnect_task = task
        task.add_done_callback(self._disconnect_cleanup_finished)

    def _handle_session_error(
        self, session: MiSession | None, error: Exception
    ) -> None:
        """Fail and tear down a session whose notification reader stopped."""
        if session is None or self._session is not session:
            return
        client = self._client
        if client is None:
            return
        _LOGGER.debug("AD1204U %s MIOT session failed: %s", self.address, error)
        self._connection_generation += 1
        self._baseline_session = None
        self._notification_publish_pending = False
        self.async_set_update_error(error)
        if self._disconnect_task is not None and not self._disconnect_task.done():
            return
        task = self.hass.async_create_background_task(
            self._cleanup_disconnected_client(client),
            name=f"ad1204u_session_error_{self.address}",
        )
        self._disconnect_task = task
        task.add_done_callback(self._disconnect_cleanup_finished)

    def _disconnect_cleanup_finished(self, task: asyncio.Task[None]) -> None:
        if self._disconnect_task is task:
            self._disconnect_task = None

    async def _cleanup_disconnected_client(self, client: BleakClient) -> None:
        async with self._lock:
            if self._client is client:
                await self._disconnect()

    async def async_shutdown(self) -> None:
        """Stop refreshes and release all BLE resources."""
        await super().async_shutdown()

        # Waiting for the lock lets an in-flight poll, write, or idle disconnect
        # finish before cleanup. Cancel the timers only after owning the lock so
        # cancellation cannot interrupt them halfway through _disconnect().
        async with self._lock:
            idle_task = self._idle_task
            self._idle_task = None
            if idle_task is not None and idle_task is not asyncio.current_task():
                idle_task.cancel()
            reconcile_task = self._reconcile_task
            self._reconcile_task = None
            if (
                reconcile_task is not None
                and reconcile_task is not asyncio.current_task()
            ):
                reconcile_task.cancel()
            await self._disconnect()

        for task in (idle_task, reconcile_task):
            if task is not None and task is not asyncio.current_task():
                with suppress(asyncio.CancelledError):
                    await task

        disconnect_task = self._disconnect_task
        if (
            disconnect_task is not None
            and disconnect_task is not asyncio.current_task()
        ):
            with suppress(asyncio.CancelledError):
                await disconnect_task

    # ---------------------------------------------------------- connection
    async def _ensure_connected(self) -> MiSession:
        if self._shutdown_requested:
            raise UpdateFailed("Coordinator is shutting down")

        if (
            self._session is not None
            and self._client is not None
            and self._client.is_connected
        ):
            if not self._session.requires_reauthentication:
                return self._session
            _LOGGER.debug(
                "reconnecting %s before MIOT encryption counter reuse",
                self.address,
            )
            await self._disconnect()

        # A missed/late disconnect callback can leave the old objects attached
        # even though the client is no longer connected. Release them before
        # establishing a replacement so setup can never overwrite resources.
        if (
            self._client is not None
            or self._auth is not None
            or self._session is not None
        ):
            await self._disconnect()

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(f"{self.address} not currently visible over Bluetooth")

        setup_generation = self._connection_generation
        _LOGGER.debug("connecting to %s", self.address)
        client = await establish_connection(
            BleakClient,
            ble_device,
            self.device_name or self.address,
            max_attempts=3,
            timeout=self._connection_timeout,
            disconnected_callback=self._on_disconnect,
        )
        self._connecting_client = client
        auth: MiAuthClient | None = None
        session: MiSession | None = None
        try:
            if self.firmware_version is None:
                self.firmware_version = await self._read_firmware_version(client)

            backend = getattr(client, "_backend", None)
            if backend is not None and hasattr(backend, "_acquire_mtu"):
                try:
                    await backend._acquire_mtu()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("_acquire_mtu failed", exc_info=True)

            auth = MiAuthClient(
                client,
                timeout=self._connection_timeout,
                bluez_start_notify=self._bluez_start_notify,
            )
            await auth.subscribe(upnp=False)
            await auth.greet()
            await auth.subscribe_upnp()
            keys = await auth.login(self._token)

            def _on_notification(plaintext: bytes) -> None:
                self._handle_notification(session, plaintext)

            def _on_fatal_error(error: Exception) -> None:
                self._handle_session_error(session, error)

            session = MiSession(
                auth,
                keys,
                timeout=self._connection_timeout,
                notification_callback=_on_notification,
                fatal_error_callback=_on_fatal_error,
            )
            await session.subscribe()
            await session.initialize()
            # Post-login settle: the per-port measurement register can lag the
            # PD contract by up to ~300 ms, so the first get_properties right
            # after login occasionally catches mid-slew bytes (e.g. in_use=1
            # with b3 reading a non-standard voltage). A short delay here
            # prevents that glitch from ever reaching HA sensors.
            await asyncio.sleep(0.3)
            if (
                self._shutdown_requested
                or setup_generation != self._connection_generation
                or not client.is_connected
            ):
                raise UpdateFailed("BLE connection changed during setup")
        except BaseException:
            if self._connecting_client is client:
                self._connecting_client = None
            cleanup_task = asyncio.create_task(
                self._cleanup_setup_resources(session, auth, client),
                name=f"ad1204u_failed_setup_{self.address}",
            )
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # A second cancellation must not strand the established BLE
                # connection. Finish the already-running bounded cleanup.
                await cleanup_task
            raise

        self._connecting_client = None
        self._connection_generation += 1
        self._client = client
        self._auth = auth
        self._session = session
        self._arm_idle_release(client)
        _LOGGER.info("AD1204U %s connected + logged in", self.address)
        return session

    async def _cleanup_setup_resources(
        self,
        session: MiSession | None,
        auth: MiAuthClient | None,
        client: BleakClient,
    ) -> None:
        """Release a connection that failed or was cancelled during setup."""
        await self._cleanup_resources(
            (
                ("session", session, "unsubscribe"),
                ("auth", auth, "unsubscribe"),
                ("client", client, "disconnect"),
            ),
            context="after incomplete setup",
        )

    async def _read_firmware_version(self, client: BleakClient) -> str | None:
        """Read the plain GATT firmware-version characteristic, if present."""
        try:
            raw = await client.read_gatt_char(FIRMWARE_VERSION_UUID)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("firmware version read failed", exc_info=True)
            return None
        return decode_firmware_version(raw)

    async def _disconnect(self) -> None:
        session, auth, client = self._session, self._auth, self._client
        if session is not None or auth is not None or client is not None:
            self._connection_generation += 1
        self._session = None
        self._auth = None
        self._client = None
        self._property_values.clear()
        self._pending_notification_values.clear()
        self._reconcile_keys.clear()
        self._baseline_session = None
        self._notification_publish_pending = False
        await self._cleanup_resources(
            (
                ("session", session, "unsubscribe"),
                ("auth", auth, "unsubscribe"),
                ("client", client, "disconnect"),
            ),
        )
        if client is not None:
            _LOGGER.info("AD1204U %s disconnected", self.address)

    async def _cleanup_resources(
        self,
        resources: tuple[tuple[str, Any | None, str], ...],
        *,
        context: str | None = None,
    ) -> None:
        """Best-effort cleanup with a finite timeout for each BLE operation."""
        timeout = min(max(self._connection_timeout, 0.1), 10.0)
        for label, obj, coro_name in resources:
            if obj is None:
                continue
            try:
                async with asyncio.timeout(timeout):
                    await getattr(obj, coro_name)()
            except TimeoutError:
                _LOGGER.warning(
                    "%s.%s() timed out after %.1fs%s",
                    label,
                    coro_name,
                    timeout,
                    f" {context}" if context else "",
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "%s.%s() failed%s",
                    label,
                    coro_name,
                    f" {context}" if context else "",
                    exc_info=True,
                )

    # ------------------------------------------------------------ writes
    @asynccontextmanager
    async def _connected_for_write(self) -> AsyncIterator[tuple[MiSession, BleakClient, int]]:
        """Own the lock, ensure a logged-in session, and map write failures.

        Every public write helper runs inside this context so connection
        setup, error translation, and teardown stay identical across them.
        """
        async with self._lock:
            try:
                session = await self._ensure_connected()
                client = self._client
                generation = self._connection_generation
                if client is None:
                    raise UpdateFailed("BLE connection disappeared before set")
                yield session, client, generation
            except UpdateFailed:
                await self._disconnect()
                raise
            except MiAuthInvalidTokenError as exc:
                await self._disconnect()
                raise ConfigEntryAuthFailed(
                    "The charger rejected the configured BLE token"
                ) from exc
            except BleakError as exc:
                await self._disconnect()
                raise UpdateFailed(f"BLE error on set: {exc}") from exc
            except Exception as exc:
                await self._disconnect()
                raise UpdateFailed(f"set failed: {exc}") from exc

    def _require_stable_connection(
        self, session: MiSession, client: BleakClient, generation: int
    ) -> None:
        """Reject a write whose connection died or was replaced mid-flight."""
        if (
            self._connection_generation != generation
            or self._session is not session
            or self._client is not client
            or not client.is_connected
        ):
            raise UpdateFailed("BLE connection changed during set")

    async def _async_confirm_write(
        self,
        session: MiSession,
        siid: int,
        piid: int,
        value: int | bool,
        *,
        u32: bool = False,
    ) -> None:
        """Send one set and require a successful set-properties response.

        The response status is the device's confirmation — the same feedback
        the Mi Home plugin waits for. A response timeout (its native error -3)
        is retried exactly once after a short pause; anything else surfaces
        immediately so HA can report the failure and keep the old state.
        """
        try:
            await set_property(
                session,
                siid,
                piid,
                value,
                u32=u32,
                timeout=WRITE_CONFIRM_TIMEOUT,
            )
            return
        except MiSessionTimeout:
            _LOGGER.debug(
                "set (siid=%s, piid=0x%02x) response timed out; retrying once",
                siid,
                piid,
            )
        await asyncio.sleep(WRITE_RETRY_DELAY)
        await set_property(
            session,
            siid,
            piid,
            value,
            u32=u32,
            timeout=WRITE_CONFIRM_TIMEOUT,
        )

    def _optimistic_property_value(
        self,
        siid: int,
        piid: int,
        value: int | bool,
        *,
        u32: bool = False,
    ) -> PropertyValue:
        """Build the PropertyValue a successful write should look like.

        The type/marker pair is copied from the last device-reported value so
        later echoes and polls compare equal (the charger reports some toggles
        as proper bools even though Mi Home writes them as u8). Without a
        prior reading, fall back to the wire encoding of our own request.
        """
        prior = self._property_values.get((siid, piid))
        if prior is not None:
            coerced = bool(value) if isinstance(prior.value, bool) else int(value)
            return PropertyValue(siid, piid, 0, prior.type_byte, prior.marker, coerced)
        if isinstance(value, bool):
            return PropertyValue(siid, piid, 0, 0x01, 0x00, value)
        if u32:
            return PropertyValue(siid, piid, 0, 0x04, 0x50, int(value))
        return PropertyValue(siid, piid, 0, 0x01, 0x10, int(value))

    def _apply_write_feedback(self, session: MiSession, item: PropertyValue) -> None:
        """Merge a confirmed write optimistically and publish immediately.

        Equivalent to the plugin's optimistic setState: entities flip as soon
        as the charger accepts the write. The device's property-changed echo
        stays authoritative — when it lands with the same value nothing is
        republished; when it disagrees the push wins.
        """
        key = item.key
        self._pending_notification_values.pop(key, None)
        changed = self._property_values.get(key) != item
        self._property_values[key] = item
        if not changed or self._baseline_session is not session:
            return
        self.async_set_updated_data(_build_snapshot(self._property_values))

    def _schedule_write_reconcile(self, *keys: tuple[int, int]) -> None:
        """Defer one bounded re-read of written properties (~3 s debounce).

        Mirrors the plugin's startPortDelayInfoTimer: a single delayed read
        covers rapid successive toggles, never blocks the caller, and only
        asks for the properties that were actually written.
        """
        self._reconcile_keys.update(keys)
        if self._reconcile_task is not None and not self._reconcile_task.done():
            self._reconcile_task.cancel()
        self._reconcile_task = self.hass.async_create_background_task(
            self._reconcile_written_properties(),
            name=f"ad1204u_write_reconcile_{self.address}",
        )

    async def _reconcile_written_properties(self) -> None:
        """Re-read recently written properties once pushes have settled."""
        try:
            await asyncio.sleep(WRITE_RECONCILE_DELAY)
            keys = frozenset(self._reconcile_keys)
            self._reconcile_keys.clear()
            if not keys or self._shutdown_requested:
                return
            async with self._lock:
                session = self._session
                client = self._client
                if (
                    session is None
                    or client is None
                    or not client.is_connected
                    or self._baseline_session is not session
                ):
                    return
                try:
                    values = await get_properties(
                        session, tuples=tuple(sorted(keys))
                    )
                except Exception:  # noqa: BLE001
                    # Best effort only: pushes plus the scheduled poll still
                    # converge the state. Never fail the write that scheduled
                    # this read or tear down the link over it.
                    _LOGGER.debug(
                        "%s post-write reconcile read failed", self.address,
                        exc_info=True,
                    )
                    return
                changed = False
                for key, item in values.items():
                    if self._property_values.get(key) != item:
                        self._property_values[key] = item
                        changed = True
                if changed and self._baseline_session is session:
                    self.async_set_updated_data(_build_snapshot(self._property_values))
        finally:
            if self._reconcile_task is asyncio.current_task():
                self._reconcile_task = None

    async def async_set_property(
        self,
        siid: int,
        piid: int,
        value: int | bool,
        *,
        u32: bool = False,
    ) -> None:
        """Write one MIOT property using app-style write feedback.

        Confirms via the set-properties response, applies the value
        optimistically, and lets the charger's echo push plus one debounced
        re-read converge the rest. No blocking refresh runs inline — the old
        settle-delay + full-poll tail raced the echo, timed out on slow links,
        and still returned success to HA.
        """
        async with self._connected_for_write() as (session, client, generation):
            await self._async_confirm_write(session, siid, piid, value, u32=u32)
            self._require_stable_connection(session, client, generation)
            item = self._optimistic_property_value(siid, piid, value, u32=u32)
            self._apply_write_feedback(session, item)
            # Explicit user activity extends the lease. Scheduled polling
            # deliberately does not, otherwise a short poll interval keeps
            # the one-peer BLE connection forever.
            self._arm_idle_release(client)
            self._schedule_write_reconcile(item.key)

    async def async_set_protocol_switch(
        self, port: str, switch: str, enabled: bool
    ) -> None:
        """Read-modify-write PIID 21 (protocol_ctl_extend) to toggle one bit.

        Bit layout (confirmed in cuktech-ble-server state.py, aligned with the
        Mi Home app's parseProtocolExtend):

            c1: pd=bit0, pps=bit1, ufcs=bit2, reserved=bit3 (always 1)
            c2: pd=bit8, pps=bit9, ufcs=bit10, reserved=bit11 (always 1)
            c3: ufcs=bit16, scp=bit17
            a:  ufcs=bit24, scp=bit25

        The reserved bit on C1/C2 is fixed to 1 by the firmware, so the
        encode helper always re-asserts it.
        """
        from .lib.protocol_switches import (
            encode_protocol_extend,
            parse_protocol_switches,
        )

        async with self._connected_for_write() as (session, client, generation):
            # Read the current u32 so we can modify a single bit atomically.
            current = self._property_values.get((2, 0x15))
            current_value = current.value if current is not None else 0
            switches = parse_protocol_switches(int(current_value))
            port_switches = switches.get(port, {})
            if switch not in port_switches and switch != "pd":
                raise ValueError(
                    f"port {port!r} does not have a {switch!r} switch"
                )
            # Only PD/PPS/UFCS/SCP are user-toggleable; the reserved bit
            # is re-asserted by encode_protocol_extend.
            if switch in ("pd", "pps", "ufcs", "scp"):
                port_switches[switch] = enabled
            switches[port] = port_switches
            new_value = encode_protocol_extend(switches)
            await self._async_confirm_write(session, 2, 0x15, new_value, u32=True)
            self._require_stable_connection(session, client, generation)
            item = self._optimistic_property_value(2, 0x15, new_value, u32=True)
            self._apply_write_feedback(session, item)
            self._arm_idle_release(client)
            self._schedule_write_reconcile(item.key)

    async def async_set_protocol_gate(self, name: str, enabled: bool) -> None:
        """Read-modify-write PIID 7 (protocol_ctrl) to toggle one global flag.

        Bit layout (Mi Home plugin parseProtocolInfo / DischargeProtocol
        composer): bit0=SCP, bit1=MiPPS, bit2=UFCS; upper bits zero. The
        official per-toggle write flips one bit and preserves the rest.
        Without a prior reading the all-on value 0b111 is assumed — the
        state the official app initializes and restores after a reset.
        """
        from .lib.protocol_switches import update_protocol_ctrl

        async with self._connected_for_write() as (session, client, generation):
            current = self._property_values.get((2, 0x07))
            current_value = current.value if current is not None else 0b111
            new_value = update_protocol_ctrl(int(current_value), name, enabled)
            await self._async_confirm_write(session, 2, 0x07, new_value)
            self._require_stable_connection(session, client, generation)
            item = self._optimistic_property_value(2, 0x07, new_value)
            self._apply_write_feedback(session, item)
            self._arm_idle_release(client)
            self._schedule_write_reconcile(item.key)

    async def async_factory_reset(self) -> None:
        """Invoke the charger's reset_setting action (siid 2 aiid 1).

        Restores charger settings to their defaults, mirroring the Mi Home
        plugin's doSettingResetAction. The action carries no arguments; the
        response status is the only confirmation, retried once on a response
        timeout like a property write. One debounced re-read of every polled
        property reconciles the new defaults afterwards — the plugin waits
        ~1 s and re-reads its protocol switches for the same reason.
        """
        async with self._connected_for_write() as (session, client, generation):
            try:
                await do_action(
                    session,
                    FACTORY_RESET_SIID,
                    FACTORY_RESET_AIID,
                    timeout=WRITE_CONFIRM_TIMEOUT,
                )
            except MiSessionTimeout:
                _LOGGER.debug("factory reset response timed out; retrying once")
                await asyncio.sleep(WRITE_RETRY_DELAY)
                await do_action(
                    session,
                    FACTORY_RESET_SIID,
                    FACTORY_RESET_AIID,
                    timeout=WRITE_CONFIRM_TIMEOUT,
                )
            self._require_stable_connection(session, client, generation)
            self._arm_idle_release(client)
            self._schedule_write_reconcile(*DEFAULT_READ_TUPLES)

    async def async_set_port_enabled(self, port: str, enabled: bool) -> None:
        """Read-modify-write PIID 16 (port_ctl) to toggle one port's enable bit.

        Bit layout: bit0=C1, bit1=C2, bit2=C3, bit3=A (1=enabled, 0=disabled).

        The bit index is ``piid - 1`` (c1=piid1→bit0, ..., a=piid4→bit3),
        matching the reference implementation in cuktech-ble-server
        (``1 << (piid - 1)``) and cuktech-ble-controller
        (``PORT_BITS = {'c1': 0, 'c2': 1, 'c3': 2, 'a': 3}``). Using the raw
        piid as the bit index was an off-by-one that toggled the *neighbour*
        port (c1→c2's bit, ..., a→bit4 outside the 4-bit mask).
        """
        from .lib.ports import PORT_PROPERTY_PIID

        piid = PORT_PROPERTY_PIID.get(port)
        if piid is None:
            raise ValueError(f"unknown port {port!r}")
        bit_index = piid - 1

        async with self._connected_for_write() as (session, client, generation):
            current = self._property_values.get((2, 0x10))
            current_value = current.value if current is not None else 0x0F
            if enabled:
                new_value = int(current_value) | (1 << bit_index)
            else:
                new_value = int(current_value) & ~(1 << bit_index)
            await self._async_confirm_write(session, 2, 0x10, new_value)
            self._require_stable_connection(session, client, generation)
            item = self._optimistic_property_value(2, 0x10, new_value)
            self._apply_write_feedback(session, item)
            self._arm_idle_release(client)
            self._schedule_write_reconcile(item.key)

    # ----------------------------------------------------------- polling
    async def _async_update_data(self) -> AD1204UData:
        async with self._lock:
            try:
                session = await self._ensure_connected()
                client = self._client
                generation = self._connection_generation
                if client is None:
                    raise UpdateFailed("BLE connection disappeared before refresh")
                self._pending_notification_values.clear()
                values = await get_properties(session)
                if (
                    self._connection_generation != generation
                    or self._session is not session
                    or self._client is not client
                    or not client.is_connected
                ):
                    raise UpdateFailed("BLE connection changed during refresh")
                values.update(self._pending_notification_values)
                self._pending_notification_values.clear()
                self._property_values = values
                self._baseline_session = session
            except UpdateFailed:
                await self._disconnect()
                raise
            except MiAuthInvalidTokenError as exc:
                await self._disconnect()
                raise ConfigEntryAuthFailed(
                    "The charger rejected the configured BLE token"
                ) from exc
            except BleakError as exc:
                await self._disconnect()
                raise UpdateFailed(f"BLE error: {exc}") from exc
            except Exception as exc:
                await self._disconnect()
                raise UpdateFailed(f"{type(exc).__name__}: {exc}") from exc

        return _build_snapshot(values)

    def _handle_notification(
        self, session: MiSession | None, plaintext: bytes
    ) -> None:
        """Merge one app-style property event into the coordinator snapshot."""
        if session is None or self._session is not session:
            return
        try:
            items = parse_notification(plaintext)
        except MiotProtocolError:
            _LOGGER.debug("ignoring unsupported MIOT event: %s", plaintext.hex())
            return

        changed = False
        for item in items:
            if item.status != 0:
                continue
            self._pending_notification_values[item.key] = item
            if self._property_values.get(item.key) != item:
                self._property_values[item.key] = item
                changed = True

        if (
            not changed
            or self._baseline_session is not session
            or self._notification_publish_pending
        ):
            return
        self._notification_publish_pending = True
        self.hass.loop.call_soon(self._publish_notification_update, session)

    def _publish_notification_update(self, session: MiSession) -> None:
        """Publish all events coalesced during the current event-loop turn."""
        self._notification_publish_pending = False
        if self._session is not session or self._baseline_session is not session:
            return
        self.async_set_updated_data(_build_snapshot(self._property_values))

    def _arm_idle_release(self, client: BleakClient) -> None:
        """Start a one-shot release timer for the current BLE connection."""
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None
        if self._idle_release <= 0:
            return
        self._idle_task = self.hass.async_create_background_task(
            self._idle_release_after(client), name=f"ad1204u_idle_{self.address}"
        )

    async def _idle_release_after(self, client: BleakClient) -> None:
        """Release one specific BLE connection after its idle lease expires."""
        try:
            await asyncio.sleep(self._idle_release)
            async with self._lock:
                # A reconnect can replace the client while an older timer is
                # sleeping. Never let that stale timer drop the new session.
                if self._client is client:
                    _LOGGER.debug(
                        "releasing BLE connection after %.1fs idle lease",
                        self._idle_release,
                    )
                    await self._disconnect()
        finally:
            if self._idle_task is asyncio.current_task():
                self._idle_task = None


def _build_snapshot(values: dict[tuple[int, int], Any]) -> AD1204UData:
    ports: dict[str, PortInfo] = {}
    for piid, port in PIID_TO_PORT.items():
        item = values.get((2, piid))
        if item is None:
            continue
        ports[port] = decode_port_info(port, item.value)

    def _u32(piid: int) -> int | None:
        item = values.get((2, piid))
        return None if item is None else int(item.value)

    def _u8(piid: int) -> int | None:
        item = values.get((2, piid))
        return None if item is None else int(item.value)

    def _bool(piid: int) -> bool | None:
        item = values.get((2, piid))
        return None if item is None else bool(item.value)

    pdo: dict[str, int | None] = {p: None for p in PORTS}
    c1c2 = _u32(0x11)
    if c1c2 is not None:
        pdo.update(decode_pdo_caps(c1c2, high_port="c1", low_port="c2"))
    c3a = _u32(0x12)
    if c3a is not None:
        pdo.update(decode_pdo_caps(c3a, high_port="c3", low_port="a"))

    total = round(sum(info.power_w for info in ports.values()), 2)
    return AD1204UData(
        ports=ports,
        total_power_w=total,
        pdo_caps_w=pdo,
        scene_mode=_u8(5),
        screen_save_time=_u8(6),
        port_ctl=_u8(0x10),
        protocol_ctl=_u8(0x07),
        protocol_ctl_extend=_u32(0x15),
        device_language=_u8(0x0d),
        usb_a_always_on=_bool(0x0f),
        screenoff_while_idle=_bool(0x13),
        screen_dir_lock=_bool(0x14),
    )
