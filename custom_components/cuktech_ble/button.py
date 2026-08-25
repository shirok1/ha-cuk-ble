"""Button entities for the AD1204U integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AD1204UConfigEntry
from .coordinator import AD1204UCoordinator
from .entity import AD1204UEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AD1204UConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities([AD1204UFactoryResetButton(coordinator)])


class AD1204UFactoryResetButton(AD1204UEntity, ButtonEntity):
    """Reset all charger settings to defaults via the reset_setting action.

    Mirrors the Mi Home plugin's settings-page "Reset to default" button:
    it invokes siid 2 aiid 1 with no arguments over BLE and lets the charger
    restore its own configuration (scene mode, protocol switches, screen
    settings, ...). The debounced re-read armed by the coordinator picks up
    the new state.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "factory_reset"

    def __init__(self, coordinator: AD1204UCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_factory_reset"

    async def async_press(self) -> None:
        await self.coordinator.async_factory_reset()
