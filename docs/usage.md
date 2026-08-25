# Usage

## Getting the BLE token

Easiest: use the **Mi Cloud (QR)** option in the config flow. It's a one-shot
fetch — no password, no stored credentials, and the integration never talks
to Mi Cloud again after setup.

If the charger later rejects a saved token, Home Assistant starts a
re-authentication flow so you can replace it without deleting the device.

If you'd rather grab the token outside Home Assistant:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[cloud-token]"
.venv/bin/python tools/ad1204u_fetch_token.py \
    --username you@example.com \
    --address AA:BB:CC:DD:EE:FF \
    --server cn
```

The script handles 2FA interactively and writes `~/.cuktech_ble.token`.
[Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
works as well — paste the 24-character hex into the manual path of the
config flow.

## Entities

One parent device per charger, with a sub-device per port (C1, C2, C3, A).
All sub-devices inherit the parent's area.

Per port: **power** (W), **voltage** (V), **current** (A), **protocol**
(diagnostic), **PDO cap** (W, diagnostic), **negotiated protocol**
(diagnostic enum), **delay power-off** (min, diagnostic — reflected from
device pushes; no setter). There's also a charger-level **total power**
sensor.

Charger-level controls (on the parent device):

- **Scene mode** (select): AI Mode / Hybrid / Single / Dual.
- **USB-A always on** (switch): keeps the USB-A rail energized (officially
  a 4-hour trickle mode).
- **Screen off when idle** (switch): charger display auto-sleeps.
- **Lock screen orientation** (switch): disables display auto-rotate.
- **SCP / MiPPS / UFCS (C1/C2)** (switches): global fast-charging protocol
  gates; see the firmware note below.
- **Reset to default** (button): restores all charger settings to their
  factory defaults (the Mi Home "Reset to default" action).

## Options

**Settings → Devices & services → CUKTECH BLE → Configure**:

| Option | Default | Range | Meaning |
|---|---|---|---|
| Reconciliation interval | 30 s | 5–3600 | Full BLE read when live property pushes are quiet |
| Idle release | 300 s | 0–3600 | Release the BLE session this long after login or the last write; scheduled polls do not extend it (`0` = stay connected) |
| Connection timeout | 15 s | 5–120 | How long to wait for a BLE connect |
| BlueZ `start-notify` hint | off | — | Linux-only workaround for CCCD flakiness |

## Notes and limits

- **Writable controls:** USB-A always on, screen-off when idle, lock screen
  orientation, scene mode, the three global protocol gates, per-port
  enable + per-port PD/PPS/UFCS/SCP protocol switches, and the factory
  reset button.
- **Firmware-version gating (not enforced in code).** The official app
  shows per-port protocol toggles (property `2.21`) only on firmware newer
  than `2.1.2_0040`, otherwise a single global gate (property `2.7`). This
  integration exposes both unconditionally — on a given firmware one set is
  the effective one and the other may no-op. See the README note.
- **Live updates:** while connected, the charger pushes changed port and
  setting values. The integration ACKs and coalesces them immediately; the
  interval above is a fallback full-state reconciliation, not the primary
  telemetry path.
- **Switch feedback:** a control flips as soon as the charger's
  set-properties response confirms the write (retried once on a response
  timeout). The charger's own property-changed push stays authoritative, and
  a single debounced re-read ~3 s later reconciles a missed push. Failed
  writes keep the previous state and surface as service-call errors. This
  mirrors the official Mi Home plugin, which never re-reads inline after a
  write.
- **One BLE peer at a time.** The charger only accepts a single Bluetooth
  client. While Mi Home is open and connected on a phone or tablet, HA
  can't reach the charger and entities will go *unavailable*. Same the
  other way: while HA is polling, Mi Home shows "Offline". To switch:
    - **HA → phone:** disable the integration (Settings → Devices &
      services → CUKTECH BLE → ⋮ → Disable). Open Mi Home; it'll
      reconnect within a few seconds.
    - **Phone → HA:** force-close Mi Home (swipe away on the phone).
      Re-enable the HA integration. If the charger doesn't pop up after
      ~30 s, unplug it for 10 s and plug it back in.
  Whoever connects first wins; some firmwares hold the
  *not-advertising* state until a power cycle.
- **Diagnostics** (Device page → ⋮ → Download diagnostics) redacts the token
  and MAC.

## Supported device

| | |
|---|---|
| Model | CUKTECH AD1204U "10 GaN Charger Ultra" |
| Mijia name | `njcuk.fitting.ad1204` |
| Mijia product id | `0x660E` |
| Service UUID | `0000fe95-0000-1000-8000-00805f9b34fb` |
