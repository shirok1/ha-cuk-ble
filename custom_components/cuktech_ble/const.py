"""Constants for the CUKTECH AD1204U Home Assistant integration."""

from __future__ import annotations

DOMAIN = "cuktech_ble"
MANUFACTURER = "CUKTECH"
MODEL = "AD1204U"
DEFAULT_DEVICE_NAME = "CUKTECH 10 GaN Charger Ultra"

CONF_BLUEZ_START_NOTIFY = "bluez_start_notify"
CONF_CONNECTION_TIMEOUT = "connection_timeout"
CONF_IDLE_RELEASE = "idle_release"
CONF_LOCAL_NAME = "local_name"
CONF_UPDATE_PERIOD = "update_period"

DEFAULT_CONNECTION_TIMEOUT = 15.0
DEFAULT_IDLE_RELEASE = 300.0
DEFAULT_UPDATE_PERIOD = 30.0

# Write feedback model, mirroring the official Mi Home plugin
# (njcuk.fitting.ad1204.js) and its BLE spec SDK:
#
# - Prefer the set-properties response when firmware emits one. Some AD1204U
#   firmware applies the write after transport RCV_OK but never emits that
#   application response, so property writes use a short optional grace period.
# - The charger's spontaneous property-changed push keeps state current
#   afterwards, so no read runs inline after a write.
# - Any reconciliation read is debounced seconds later (the app's
#   startPortDelayInfoTimer uses 1.5-3 s), never in the caller's path.
WRITE_CONFIRM_TIMEOUT = 5.0  # bound one set-properties response wait
WRITE_RETRY_DELAY = 0.1  # pause before the single confirmation retry
WRITE_RESPONSE_GRACE = 0.5  # optional set-response wait after transport RCV_OK
WRITE_RECONCILE_DELAY = 3.0  # debounce before the post-write re-read
