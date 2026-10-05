"""Firmware capability selection and entity categories using HA's registry."""

from types import SimpleNamespace

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cuktech_ble.const import DOMAIN
from custom_components.cuktech_ble.coordinator import AD1204UCoordinator
from custom_components.cuktech_ble.select import (
    AD1204UDeviceLanguageSelect,
    AD1204UScreenSaveTimeSelect,
)
from custom_components.cuktech_ble.switch import (
    async_setup_entry,
    AD1204UProtocolSwitch,
    AD1204UProtocolGateSwitch,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


@pytest.mark.parametrize(
    "version,ports,gates,enabled",
    [
        ("2.1.2_0040", 0, 3, True),
        ("2.1.2_0073", 10, 0, True),
        (None, 10, 3, False),
    ],
)
async def test_firmware_selects_controls_and_categories(
    hass, version, ports, gates, enabled
):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = AD1204UCoordinator(
        hass,
        config_entry=entry,
        address=ADDRESS,
        token=b"0" * 12,
        name="Charger",
        update_interval=30,
        idle_release=0,
        connection_timeout=15,
        bluez_start_notify=False,
    )
    coordinator.firmware_version = version
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    assert sum(isinstance(e, AD1204UProtocolSwitch) for e in entities) == ports
    assert sum(isinstance(e, AD1204UProtocolGateSwitch) for e in entities) == gates
    for entity in entities:
        if isinstance(entity, (AD1204UProtocolSwitch, AD1204UProtocolGateSwitch)):
            assert entity.entity_registry_enabled_default is enabled
        elif entity.unique_id.endswith("_port_enable"):
            assert entity.entity_category is None
        elif entity.unique_id.endswith(("screenoff_while_idle", "screen_dir_lock")):
            assert entity.entity_category is EntityCategory.CONFIG
    assert (
        AD1204UDeviceLanguageSelect(coordinator).entity_category
        is EntityCategory.CONFIG
    )
    assert (
        AD1204UScreenSaveTimeSelect(coordinator).entity_category
        is EntityCategory.CONFIG
    )
    await coordinator.async_shutdown()


async def test_firmware_change_disables_legacy_controls_and_preserves_user_choices(
    hass,
):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch", DOMAIN, f"{ADDRESS}_protocol_gate_scp", config_entry=entry
    )
    user_disabled = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{ADDRESS}_protocol_gate_mipps",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    coordinator = AD1204UCoordinator(
        hass,
        config_entry=entry,
        address=ADDRESS,
        token=b"0" * 12,
        name="Charger",
        update_interval=30,
        idle_release=0,
        connection_timeout=15,
        bluez_start_notify=False,
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    coordinator.firmware_version = "2.1.2_0073"
    await async_setup_entry(hass, entry, lambda entities: None)
    assert (
        registry.async_get(old.entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert (
        registry.async_get(user_disabled.entity_id).disabled_by
        is er.RegistryEntryDisabler.USER
    )
    coordinator.firmware_version = "2.1.2_0040"
    await async_setup_entry(hass, entry, lambda entities: None)
    assert registry.async_get(old.entity_id).disabled_by is None
    assert (
        registry.async_get(user_disabled.entity_id).disabled_by
        is er.RegistryEntryDisabler.USER
    )
    await coordinator.async_shutdown()
