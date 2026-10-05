# CUKTECH BLE for Home Assistant

Local Home Assistant integration for the **CUKTECH AD1204U "10 GaN Charger
Ultra"**. Reads per-port power, voltage, current, and protocol over BLE, and
exposes a small set of charger settings (scene mode, USB-A always on,
screen toggles) as HA controls. Runtime control and telemetry stay local over
BLE; the optional QR setup makes a one-time Xiaomi Cloud request to retrieve
the pairing token.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zuyan9&repository=ha-cuk-ble&category=integration)
[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cuktech_ble)

## Install

Requires Home Assistant 2025.3 or newer.

1. Click **Open in HACS** above (or add this repo as an Integration custom
   repository in HACS), install, and restart Home Assistant.
2. Click **Add integration** above, pick **CUKTECH BLE**, and either:
   - log in with the Mi Cloud QR flow to fetch the BLE pairing token, or
   - paste an existing 24-character hex token.

That's it — HA creates one device per charger with sensors for each port.

## Firmware version and feature availability

Fast-charging controls follow the firmware capabilities used by Mi Home:

- Firmware **newer than `2.1.2_0040`** exposes per-port protocol switches.
- Firmware **`2.1.2_0040` or older** exposes global SCP / MiPPS / UFCS-C1/C2
  switches.
- If the firmware version cannot be identified, both sets are available as
  **disabled-by-default** entities for manual testing.

Previously registered controls that do not match the firmware are disabled.
Explicit user-disabled controls stay disabled when the firmware changes.

The negotiated protocol, C3&A merged-output, and delay power-off sensors are
read-only diagnostics populated from device pushes. Values may require newer
firmware or an app interaction.

Setup verifies the local Bluetooth connection before saving the key. If the key
is rejected later, reauthentication offers the same Mi Home QR login and manual
entry choices without recreating the device.

## More

- [Usage, options, and getting the token manually](docs/usage.md)
- [Protocol notes and reverse-engineering](docs/reverse-engineering.md)
- [Mi Home plugin parity audit](docs/mihome-plugin-parity.md)
- [Roadmap / open questions](ROADMAP.md)
- [Issues / PRs](https://github.com/zuyan9/ha-cuk-ble/issues)

Unofficial; not affiliated with CUKTECH or Xiaomi.

## Development

Install the regular development environment and run the fast checks with:

```shell
uv sync --extra test
uv run ruff check custom_components tests tests_ha
uv run python -m pytest -q
```

The current Home Assistant stubs require Python 3.14.2 or newer. Type-check the
integration with:

```shell
uv run ty check
```
