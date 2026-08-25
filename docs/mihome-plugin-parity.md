# Mi Home plugin parity audit (njcuk.fitting.ad1204.js)

Line-by-line comparison of the official Mi Home React-Native plugin bundle
(`/drive/ep990/njcuk.fitting.ad1204.js`, 34 206 lines, module map at the end)
and the MIOT SDK bundle (`sdk.js`) against what this integration implements.
Every claim carries absolute line numbers into the plugin bundle unless noted.

Audit date: 2026-08-25. Sources: full reads of MainPage (556–6434),
MHSetting (24063–26143), privateUtils (22117–22937), PortCtlPage
(29607–32563), ScreenSettingPage (32664–33882), Upgrade (33883–34206),
CommonUtils (6741–7685), string tables (9370–9684), plus targeted SDK greps.

---

## 1. Feature gap summary (ranked)

| # | Feature | Official behaviour | Our status |
|---|---------|--------------------|------------|
| 1 | **Per-port delay power-off** | 2.8 enable bitmap + 2.9–2.12 remaining **minutes**; batched 2-object write; default 2 h; 0 disables | ❌ missing |
| 2 | **`enter` handshake (2.14)** | write `value 2` (u8) right after login, before version read; retried on failed reads | ❌ never sent |
| 3 | **Factory reset action** | `siid 2 aiid 1`, empty objects, BLE spec doAction; re-write of 2.7 after ~1 s | ❌ missing (needs action frames) |
| 4 | **screen_save_time enum drift** | current bundle writes `{5:1min, 1:5min, 2:10min, 3:30min, 4:always}` | ⚠️ ours is `{4,0,1,2,3}` — same list shifted by one (see §3.1) |
| 5 | **Negotiated-protocol labels** | 2.17/2.18 kind byte → {1:"5V",2:"5V",3:QC,4:AFC,5:FCP,6:SCP,7:PD,8:PPS,9:PPS,10:UFCS}; powers ignored by app | ⚠️ we only map 0x07/0x08 kinds |
| 6 | **Global protocol gate (2.7)** | writable u8: bit0 SCP, bit1 **MiPPS**, bit2 UFCS (UFCS scoped C1/C2); used on pre-V2 firmware | ⚠️ diagnostic-only |
| 7 | **Firmware-gated features** | `isSpecV2()` = `fw > "2.1.2_0040"` (string compare): V2 → 2.21 per-port toggles; V1 → 2.7 page instead | ❌ no gating |
| 8 | **C3&A merged mode** | C3 status byte `0x11` = combined slot; UI merges cards, suppresses A row | ❌ reported as plain in-use |
| 9 | **Batched property writes** | several flows write 2 objects in one frame (2.8+2.9..12; 2.7+2.6) | ❌ single-property only |
| 10 | **OTA availability** | cloud latest version vs GATT current, lexicographic compare; native upgrade page kills all ports | ❌ nothing (transport not feasible locally) |

Polish-level items: echo-suppression windows after local writes (2000 ms on
2.16 pushes, PortCtlPage L30049–30060), `-3` transient-error fast retry
(50–100 ms) and `-4002/-5001` rollback conventions everywhere, per-(siid,piid)
in-flight mutex with 3 s default timeout (CommonUtils L7295–7343),
anti-double-tap debounce clamped [2,5] over a 300 ms window.

## 2. What the audit validated in our implementation ✅

- **Port-info u32 layout** (2.1–2.4): MSB-first `V<<24 | I<<16 | proto<<8 | status`,
  ×0.1 V / ×0.1 A, power computed client-side as V×I
  (`parsePortInfo` 5391–5455). Byte-wise identical to our LE b0–b3 decode.
- **port_ctl bitmap (2.16)**: bit0=C1, bit1=C2, bit2=C3, bit3=A
  (`parsePortCtl` 30033–30093, composer 30920–30979). Matches our switches.
- **protocol_ctl_extend (2.21)**: byte0=C1/byte1=C2 → bit0 PD, bit1 PPS,
  bit2 UFCS, **bit3 force-set `0x08` on every write** (ignored on read);
  byte2=C3/byte3=A → bit0 UFCS, bit1 SCP; compose `(a<<24)|(c3<<16)|(c2<<8)|c1`,
  written as type 5 (u32) (`setProtocolExtend` 31541–31571). Our
  `encode_protocol_extend()` emits exactly this.
- **Scene mode (2.5)**: values 1–4 only; 1=AI Mode, 2=Hybrid (数码生态,
  "Apple ecosystem"), 3=Single (极速单充), 4=Dual (均衡输出)
  (strings 9409–9412, SceneMain 28094–28107). Our select matches.
- **Device language (2.13)**: 0=English, 1=Chinese, writable u8
  (`setScreenLanguageMode` 25022–25041). Matches.
- **2.19 / 2.20**: bools written as u8 `1/0` type 1 (33215–33222, 33341–33348) —
  matches our `kind="u8"` switch encoding.
- **usb_a_always_on (2.15)**: real JS boolean, `type 0`
  (5648–5654) — matches our bool wire encoding.
- **Total power** is a client-side sum; there is **no device-side total-power
  property** — our derived sensor is the only possible form.
- **Firmware version** comes from the plain GATT characteristic
  (`bleDevice.getVersion(true)` 25248–25295) — same source we use.
- **Push-driven + reconcile design**: official subscribes all 21 `prop.2.*`
  keys (6514–6560) and batch-reads after connect (5838–6168) — mirrors our
  notification merge + periodic reconciliation.
- **No exponential backoff** in official reconnect logic (fixed 300 ms–6 s
  delays, error-specific; 5065–5221) — our simpler policy is defensible.

## 3. Corrections & risks

### 3.1 screen_save_time has TWO encodings (+1 shift) — verify on hardware

Current bundle (`_getScreenOffMode` 32916–32947, inverse `_getScreenOffIndex`
32950–32981, options list 32775–32781):

| Option | picker idx | device value written |
|--------|-----------|----------------------|
| 1 Min  | 0 | **5** |
| 5 Min  | 1 | 1 |
| 10 Min | 2 | 2 |
| 30 Min | 3 | 3 |
| Always-On | 4 | 4 |

Round-trip verified: pick "1 Min" → write 5 → push `prop.2.6=5` →
`_getScreenOffIndex(5)=0` renders "1 Min".

Our select maps `{4:1_min, 0:5_min, 1:10_min, 2:30_min, 3:always_on}`
(MIOT-spec value lists + a 2026-04 tablet capture). Note the perfect +1
shift between the two tables — same ordered enum, 0-based vs 1-based.
Likely explanation: firmware ≤ `2.1.2_0040` uses the 0-based spec-v1 codes,
newer firmware the 1-based ones (the app branches everything else on
`isSpecV2()`, CommonUtils 7111–7126). **If a user's charger is on newer
firmware, our select writes the wrong timeout class** (e.g. "always on"
→ device takes 3 = "30 min"). Action: probe on hardware — read back after a
set, or gate the mapping on the firmware version string like the app.

### 3.2 usb_a_always_on is a 4-hour trickle timer

Official label/hint: "USB-A trickle charging" / "USB-A on for 4 hours"
(9414–9415, zh 9574–9575). Not a permanent always-on. Rename/re-document the
switch and consider noting auto-off behaviour.

### 3.3 The live `b1` protocol byte is never named by the official app

MainPage renders port protocol text from **2.17/2.18 kind bytes**
(`getC1C2ProtocolStr(protocolC1…)` 3359/3434/3516/3593; state fed only by
`parseC1C2/C3AProtocolInfo` 5487/5521). The b1 byte inside 2.1–2.4 is used
only for `=== 0` checks ("no negotiation" icon, 2481–2524) and to seed port
switch state (29749–29753). Our `PROTOCOL_NAMES` table remains our own
reverse-engineering (fine — richer than official), but a **negotiated
protocol sensor from 2.17/2.18 with official labels** would be authoritative.

### 3.4 2.17/2.18 are never written — closes a ROADMAP item

Grep over the whole bundle: zero writes to `c1_c2_protocol`/`c3_a_protocol`;
only read params (5937/5941) and subscriptions (6546–6547). The ROADMAP's
"per-port protocol masks (u32 writes, piid 0x11/0x12)" research item has no
official counterpart — the app treats them as read-only contract reports.
The "power byte" we expose as `pdo_cap` is decoded-but-discarded by the app;
our SINK240 sweep remains the evidence it tracks watts.

### 3.5 Negative findings (stop looking)

- **No temperature property anywhere** in plugin or exposed by any page
  (bundle-wide grep). Confirms docs/properties.md conclusion.
- **No fault system**: `FAULT_CODE`/`CHARGER_STATUS` enums exist
  (privateUtils 22177–22199; DC/AC/PV entries betray an inherited
  power-station template) but have zero consumers; `this.fault[16]`,
  `fault_count`, `fault_protect` are initialised and reset but never
  populated (887, 762–763, 5823–5830).
- **No energy counters/history on device**: both charts accumulate samples in
  RAM at 1 Hz for 60 s (`ChartTimer` 4706–4726, arrays 877–881, Y-max 140 W);
  x-axis labels are fake minute marks. HA's recorder already supersedes this.
- **OTA transport is native/cloud** (`openBleCommonDeviceUpgradePage`
  auth_type 4, 34068–34121; routing CommonSetting 25251–25291; SDK audit:
  no JS-side BLE OTA at all). Only version display/compare is reproducible.

## 4. Detail: the three missing features

### 4.1 Per-port delay power-off (PortCtlPage)

- Read: init batch `[2.8, 2.9, 2.10, 2.11, 2.12, 2.1–2.4, 2.16]`
  (30255–30297), steady-state `[2.8, 2.9–2.12]`; poll 20 s active /
  3 s failed-or-idle-skip (30442/30470/30231–30249).
- Semantics: 2.9–2.12 = **remaining minutes** per port (startH=floor(v/60),
  startM=v%60, 30342–30346). The 2.8 read value is **ignored** by the app
  (`case 0: break`, 30335–30336); on/off state derives from minutes ≠ 0.
- Write: ONE transaction, two objects — `{2.8, bitmap, type 1}` +
  `{piid 9+port, minutes, type 3}` (30569–30582). Bitmap =
  `1<<port` for each port with minutes > 0 (29892–29908).
  Enable-without-time defaults to **120 min** (30524–30532); disable writes 0.
- HA shape suggestion: a number/select entity per port (off / 30 m … 23 h 59 m
  or h+m pair) backed by the coordinator, or a `delay_off` service taking
  minutes; must write both objects atomically (→ needs batched writes, §4.4).

### 4.2 `enter` (2.14) handshake — answers the ROADMAP open question

- Single write site `sendEnterAppTODev` (5254–5388): `{siid:2, piid:14,
  value:2, type:1}`, guarded by the usual mutex, 6 s timeout with one
  automatic retry (5361–5375).
- Triggered: once per connection when focused (4455–4458), and as recovery
  after two failed `getDeviceInfo` attempts (6146–6152). Never on exit.
- On success: clears `needEnterAppNotify`, then reads GATT firmware version
  (5338–5340). Name/log strings ("enter sendEnterAppTODev") indicate
  "app attached / is watching" notify to the charger.
- We currently skip it; captures show writes succeed without it, so it is
  optional — but replicating it (post-login, pre-first-read) maximises
  fidelity and may influence device push/UI-sleep behaviour. Cheap to add in
  `MiSession.initialize()` / `_ensure_connected()`.

### 4.3 reset_setting action + writable 2.7

- `doSettingResetAction` (25733–25782): `Bluetooth.spec.doAction(mac,
  {"siid":2,"aiid":1,"objects":[]})`; success → re-read protocol switches
  after 1000 ms. Backed by the settings page "Reset to default" button
  (24479–24536).
- Requires action frames in our session (SDK confirms BLE doAction = spec
  opcode 5, blespec.js L64386; success code 0, errors `-(1000·src+cause)`).
- 2.7 `protocol_ctrl` is writable (bit0 SCP / bit1 MiPPS / bit2 UFCS, upper
  bits zero): per-toggle writes on the legacy V1 page (27333–27698) and the
  post-reset restore chain (25785–25999). UFCS scope is C1/C2 only
  (hint key `only_c1c2`, 9507). HA could expose a "UFCS (C1/C2)" switch on
  V1 firmware and/or a diagnostic-ish bitmask switch generally.

## 5. Reference facts worth keeping

- Wire `type` codes used by the plugin: **0** = bool (2.15), **1** = u8-class
  (2.5/2.6/2.7/2.8/2.13/2.14/2.16/2.19/2.20), **3** = minutes u32-class
  (2.9–2.12), **5** = u32 (2.21). Consistent with our marker scheme
  (bool 0x01/0x00, u8 0x01/0x10, u32 0x04/0x50).
- Official batch read after connect: 2.1–2.4, 2.5, 2.6, 2.7, 2.15, 2.13,
  2.21, 2.19, 2.20, 2.17, 2.18, 2.16 (order 5886–5947). Our
  DEFAULT_READ_TUPLES covers the same set.
- Status byte: 0 = off/no-output ("--" rendering 3335–3337), 1 = active
  (2477–2508), **0x11 on C3 = C3+A merged output** (5422–5432, merged card
  32063–32249). Consider exposing a merged-mode flag and suppressing the A
  entity while merged.
- Version handling: current from GATT char or spec 1.4; latest from cloud
  `getLatestVersionV2`; comparisons are **plain lexicographic string
  compares**, including the V2 gate vs `"2.1.2_0040"` (6574, 7111–7126).
- Scene-mode official power hints (nice metadata for our select):
  Hybrid = C1 MacBook + C2 iPhone/iPad (+C3 iPhone +A AirPods);
  Single = 100 W laptop priority + 20 W others; Dual = 60+60 W or
  45+45 W laptops (+C3 33 W) (9438–9460).

## 6. Plugin module map (for future reference)

| Lines | Module | Content |
|---|---|---|
| 36–555 | App entry | mesh subscribe, OTA auto-check config |
| 556–6434 | MainPage | telemetry, scenes, chart, enter handshake |
| 6435–6740 | MHGlobalData | **siid/piid map**, subscription list |
| 6741–7685 | CommonUtils | isSpecV2, write mutex, CARD_TYPE flags |
| 9370–9684 | i18n en/zh | all official labels/hints |
| 11291–12424 | BallAnimation | decorative |
| 12425–18961 | UI kit + LineChart | 4-series 60 s rolling chart |
| 18962–22116 | UI kit | TimePicker, VoiceCommands, PowerBarChart |
| 22117–22937 | privateUtils | transport helpers, error tables, enums |
| 24063–26143 | MHSetting | language, reset, OTA entry, connect logic |
| 26244–27169 | DebugInfo | dev page, cloud timer scenes |
| 27196–27946 | DischargeProtocol | V1 global UFCS/SCP/MiPPS page |
| 27948–29305 | SceneMain | scene-mode cards |
| 29607–32563 | PortCtlPage | port on/off, delay-off, 2.21 toggles |
| 32664–33882 | ScreenSettingPage | 2.6/2.19/2.20 |
| 33883–34206 | Upgrade | native OTA hand-off |
