"""Switch entities for writable charger booleans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers import entity_registry as er

from . import AD1204UConfigEntry
from .coordinator import AD1204UCoordinator, AD1204UData
from .entity import AD1204UEntity
from .const import DOMAIN
from .lib.firmware import uses_port_protocols
from .lib.ports import PORTS, PORT_CTL_BIT
from .lib.protocol_switches import protocol_ctrl_enabled, protocol_switch_enabled


@dataclass(frozen=True, kw_only=True)
class AD1204USwitchDescription(SwitchEntityDescription):
    siid: int
    piid: int
    getter: Callable[[AD1204UData], bool | None]
    # Mi Home sends some "bool"-looking toggles as u8 0/1 (type=0x01 marker=0x10)
    # rather than the proper bool encoding (marker=0x00). Match that so writes
    # are byte-identical to the vendor app — confirmed via tablet capture on
    # 2026-04-23.
    kind: str = "bool"  # "bool" or "u8"


SWITCHES: tuple[AD1204USwitchDescription, ...] = (
    AD1204USwitchDescription(
        key="usb_a_trickle_charging",
        translation_key="usb_a_trickle_charging",
        siid=2,
        piid=0x000F,
        getter=lambda data: data.usb_a_always_on,
    ),
    AD1204USwitchDescription(
        key="screenoff_while_idle",
        entity_category=EntityCategory.CONFIG,
        translation_key="screenoff_while_idle",
        siid=2,
        piid=0x0013,
        kind="u8",
        getter=lambda data: data.screenoff_while_idle,
    ),
    AD1204USwitchDescription(
        key="screen_dir_lock",
        entity_category=EntityCategory.CONFIG,
        translation_key="screen_dir_lock",
        siid=2,
        piid=0x0014,
        kind="u8",
        getter=lambda data: data.screen_dir_lock,
    ),
)


def _port_enabled_getter(port: str) -> Callable[[AD1204UData], bool | None]:
    bit = PORT_CTL_BIT[port]

    def _get(data: AD1204UData) -> bool | None:
        if data.port_ctl is None:
            return None
        return bool(data.port_ctl & (1 << bit))

    return _get


# Per-port protocol switches derived from PIID 21 (protocol_ctl_extend).
# Only PD/PPS/UFCS/SCP are user-toggleable; the reserved bit is firmware-fixed.
PROTOCOL_SWITCHES_PER_PORT: dict[str, tuple[str, ...]] = {
    "c1": ("pd", "pps", "ufcs"),
    "c2": ("pd", "pps", "ufcs"),
    "c3": ("ufcs", "scp"),
    "a": ("ufcs", "scp"),
}


def _protocol_switch_getter(
    port: str, switch: str
) -> Callable[[AD1204UData], bool | None]:
    def _get(data: AD1204UData) -> bool | None:
        if data.protocol_ctl_extend is None:
            return None
        return protocol_switch_enabled(data.protocol_ctl_extend, port, switch)

    return _get


# Global protocol gates from PIID 7 (protocol_ctrl): bit0=SCP, bit1=MiPPS,
# bit2=UFCS (UFCS affects C1/C2 only). Writable — the official app toggles
# these on pre-spec-V2 firmware and after a factory reset.
PROTOCOL_GATES: tuple[str, ...] = ("scp", "mipps", "ufcs")


def _protocol_gate_getter(gate: str) -> Callable[[AD1204UData], bool | None]:
    def _get(data: AD1204UData) -> bool | None:
        if data.protocol_ctl is None:
            return None
        return protocol_ctrl_enabled(data.protocol_ctl, gate)

    return _get


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AD1204UConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = [
        AD1204USwitch(coordinator, description=description) for description in SWITCHES
    ]
    # Per-port enable switches (PIID 16 bitmap, read-modify-write).
    for port in PORTS:
        entities.append(
            AD1204UPortEnableSwitch(
                coordinator,
                port=port,
                getter=_port_enabled_getter(port),
            )
        )
    # Per-port protocol switches (PIID 21 u32, read-modify-write).
    for port in PORTS:
        for switch in PROTOCOL_SWITCHES_PER_PORT[port]:
            entities.append(
                AD1204UProtocolSwitch(
                    coordinator,
                    port=port,
                    switch_name=switch,
                    getter=_protocol_switch_getter(port, switch),
                )
            )
    # Global protocol gates (PIID 7 u8, read-modify-write).
    for gate in PROTOCOL_GATES:
        entities.append(
            AD1204UProtocolGateSwitch(
                coordinator,
                gate=gate,
                getter=_protocol_gate_getter(gate),
            )
        )
    port_protocols = uses_port_protocols(coordinator.firmware_version)
    registry = er.async_get(hass)
    supported = []
    for entity in entities:
        is_port_protocol = isinstance(entity, AD1204UProtocolSwitch)
        if is_port_protocol or isinstance(entity, AD1204UProtocolGateSwitch):
            # Keep unknown firmware opt-in; preserve any explicit user choice.
            entity._attr_entity_registry_enabled_default = port_protocols is not None
            assert entity.unique_id is not None
            entity_id = registry.async_get_entity_id("switch", DOMAIN, entity.unique_id)
            registered = registry.async_get(entity_id) if entity_id else None
            incompatible = (
                port_protocols is not None and is_port_protocol != port_protocols
            )
            if incompatible:
                if registered is not None and registered.disabled_by is None:
                    registry.async_update_entity(
                        registered.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
                    )
                continue
            if (
                registered is not None
                and registered.disabled_by is er.RegistryEntryDisabler.INTEGRATION
                and port_protocols is not None
            ):
                registry.async_update_entity(registered.entity_id, disabled_by=None)
        supported.append(entity)
    async_add_entities(supported)


class AD1204USwitch(AD1204UEntity, SwitchEntity):
    entity_description: AD1204USwitchDescription

    def __init__(
        self,
        coordinator: AD1204UCoordinator,
        *,
        description: AD1204USwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.getter(data)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._write(False)

    async def _write(self, value: bool) -> None:
        if self.entity_description.kind == "u8":
            # Send as u8 (type=0x01 marker=0x10) to match Mi Home's wire format.
            payload: int | bool = int(value)
        else:
            payload = value
        await self.coordinator.async_set_property(
            self.entity_description.siid,
            self.entity_description.piid,
            payload,
        )


class AD1204UPortEnableSwitch(AD1204UEntity, SwitchEntity):
    """Per-port power enable switch backed by PIID 16 (port_ctl bitmap)."""

    def __init__(
        self,
        coordinator: AD1204UCoordinator,
        *,
        port: str,
        getter: Callable[[AD1204UData], bool | None],
    ) -> None:
        super().__init__(coordinator, port=port)
        self._port = port
        self._getter = getter
        self.entity_description = SwitchEntityDescription(
            key=f"{port}_port_enable",
            translation_key="port_enable",
        )
        self._attr_unique_id = f"{coordinator.address}_{port}_port_enable"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self._getter(data)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self.coordinator.async_set_port_enabled(self._port, True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.coordinator.async_set_port_enabled(self._port, False)


class AD1204UProtocolSwitch(AD1204UEntity, SwitchEntity):
    """Per-port fast-charging protocol switch backed by PIID 21 (u32)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: AD1204UCoordinator,
        *,
        port: str,
        switch_name: str,
        getter: Callable[[AD1204UData], bool | None],
    ) -> None:
        super().__init__(coordinator, port=port)
        self._port = port
        self._switch_name = switch_name
        self._getter = getter
        self.entity_description = SwitchEntityDescription(
            key=f"{port}_{switch_name}_protocol",
            translation_key=f"protocol_{switch_name}",
        )
        self._attr_unique_id = f"{coordinator.address}_{port}_{switch_name}_protocol"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self._getter(data)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self.coordinator.async_set_protocol_switch(
            self._port, self._switch_name, True
        )

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.coordinator.async_set_protocol_switch(
            self._port, self._switch_name, False
        )


class AD1204UProtocolGateSwitch(AD1204UEntity, SwitchEntity):
    """Global protocol gate backed by PIID 7 (protocol_ctrl) bit toggling."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: AD1204UCoordinator,
        *,
        gate: str,
        getter: Callable[[AD1204UData], bool | None],
    ) -> None:
        super().__init__(coordinator)
        self._gate = gate
        self._getter = getter
        self.entity_description = SwitchEntityDescription(
            key=f"protocol_gate_{gate}",
            translation_key=f"protocol_gate_{gate}",
        )
        self._attr_unique_id = f"{coordinator.address}_protocol_gate_{gate}"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self._getter(data)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self.coordinator.async_set_protocol_gate(self._gate, True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.coordinator.async_set_protocol_gate(self._gate, False)
