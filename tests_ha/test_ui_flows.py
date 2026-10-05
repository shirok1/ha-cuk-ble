"""User-facing connection checks, recovery and advanced option isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, SOURCE_REAUTH
from homeassistant.const import CONF_ADDRESS, CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cuktech_ble.config_flow import AD1204UConfigFlow
from custom_components.cuktech_ble.const import DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"
TOKEN = "ab" * 12
VALIDATE = "custom_components.cuktech_ble.config_flow.AD1204UCoordinator.async_validate_connection"


@pytest.mark.parametrize("cloud", [False, True])
@pytest.mark.parametrize(
    "failure,error",
    [(ConfigEntryAuthFailed(), "invalid_auth"), (UpdateFailed(), "cannot_connect")],
)
async def test_connection_failure_does_not_save_entry(hass, cloud, failure, error):
    flow = AD1204UConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_USER}
    flow._address = ADDRESS
    flow._fetched_token = TOKEN
    with patch(VALIDATE, new=AsyncMock(side_effect=failure)):
        result = (
            await flow.async_step_cloud_done()
            if cloud
            else await flow.async_step_token({CONF_TOKEN: TOKEN})
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base" if cloud else CONF_TOKEN: error}
    assert not hass.config_entries.async_entries(DOMAIN)
    if cloud:
        assert "reason" not in (result.get("description_placeholders") or {})


async def test_manual_success_validates_before_create(hass):
    flow = AD1204UConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_USER}
    flow._address = ADDRESS
    with patch(VALIDATE, new=AsyncMock()) as validate:
        result = await flow.async_step_token({CONF_TOKEN: TOKEN})
    validate.assert_awaited_once()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == TOKEN


async def test_cloud_reauth_updates_existing_entry_only_after_validation(
    hass, enable_custom_integrations
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_TOKEN: "00" * 12},
        unique_id=ADDRESS,
    )
    entry.add_to_hass(hass)
    flow = AD1204UConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    result = await flow.async_step_reauth(dict(entry.data))
    assert result["menu_options"] == ["cloud", "manual"]
    assert (await flow.async_step_cloud())["step_id"] == "cloud_region"
    flow._fetched_token = TOKEN
    with patch(VALIDATE, new=AsyncMock(side_effect=ConfigEntryAuthFailed())):
        result = await flow.async_step_cloud_done()
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_TOKEN] == "00" * 12
    with (
        patch(VALIDATE, new=AsyncMock()),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await flow.async_step_cloud_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == TOKEN
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    reload.assert_called_once_with(entry.entry_id)


async def test_general_options_preserve_advanced_settings(
    hass, enable_custom_integrations
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_TOKEN: TOKEN},
        options={
            "bluez_start_notify": True,
            "idle_release": 0,
            "connection_timeout": 40,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["general", "advanced"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    assert {str(key) for key in result["data_schema"].schema} == {"update_period"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_period": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "update_period": 60,
        "bluez_start_notify": True,
        "idle_release": 0,
        "connection_timeout": 40,
    }


async def test_reauth_validation_uses_existing_connection_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_TOKEN: TOKEN},
        options={"bluez_start_notify": True, "connection_timeout": 40},
    )
    entry.add_to_hass(hass)
    flow = AD1204UConfigFlow()
    flow.hass = hass
    flow.context = {"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    await flow.async_step_reauth(dict(entry.data))
    with patch(
        "custom_components.cuktech_ble.config_flow.AD1204UCoordinator",
        return_value=SimpleNamespace(async_validate_connection=AsyncMock()),
    ) as coordinator:
        assert await flow._validate_token(TOKEN) is None
    assert coordinator.call_args.kwargs["bluez_start_notify"] is True
    assert coordinator.call_args.kwargs["connection_timeout"] == 40
