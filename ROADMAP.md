# Roadmap

Ordered by "finishable as a single unit" and by what each one unlocks.
Agent-local details live in `.claude/TestInstructions.md` (gitignored); this
file is the shareable summary.

## Quick wins

- [x] **Cut a real release tag** (`v0.3.0` or similar). HACS currently shows
  commit SHAs to users; tagged releases give them version numbers and a
  rendered changelog.
- [x] **Formalize the decrypt-set_properties flow into
  `tools/decrypt_btsnoop_miot.py`.** The inline Python heredoc we've used
  should become a script that takes a btsnoop log plus token and emits one
  row per decrypted frame (`ts, opcode, siid, piid, value`).
- [x] **Tablet-unlock helper.** The sendevent `L` pattern we scripted during
  the 2026-04-23 Mi Home capture belongs in `tools/tablet_unlock.sh` so
  future captures don't re-derive touchscreen coordinates.

## Research that needs live hardware

- [ ] **`b1` under real load.** Plug a phone/laptop into C1 drawing ≥1 A and
  check whether `b1` shifts from `0x0a` to `0x01/0x03/0x08` as earlier
  captures hinted. Closes the protocol-name sensor's accuracy gap.
- [ ] **`b0` upper nibble.** Cable-flip and port-swap with the same sink
  under the same contract; upper-nibble toggle vs orientation resolves
  CC polarity vs port-index. (The `0x11` C3 case is now confirmed as the
  merged C3&A slot — exposed as a binary sensor.)
- [ ] **`screen_save_time` firmware dependent** — the official plugin uses a
  +1-shifted value set (`5`/`1`/`2`/`3`/`4` = 1/5/10/30min/always); probe a
  write's read-back to pin the current firmware's encoding before changing
  the HA select mapping.

## Newer-firmware features (2026-08-25 plugin audit — implemented)

- [x] **`enter` handshake (piid 0x0e)** — it's an app-attach notify, written
  `2` once per connection right after login (re-sent when reads fail). The
  integration now sends it on connect. Closes the old open question.
- [x] **Global protocol gates (piid 0x07)** — writable u8: bit0=SCP,
  bit1=MiPPS, bit2=UFCS (UFCS = C1/C2 only); exposed as SCP/MiPPS/UFCS
  switches.
- [x] **Negotiated protocol sensors (piids 0x11/0x12)** — per-port
  diagnostic enum from the official kind-byte table. These piids are
  **never written by the app** — the old "u32 writes" roadmap item is dead.
- [x] **C3&A merged-output binary sensor** (C3 status byte 0x11).
- [x] **Factory reset button** (`siid 2 aiid 1` over BLE doAction).
- [x] **Delay power-off diagnostics (piids 0x08–0x0c)** — push-only per-port
  remaining-minutes sensors; deliberately no polling and no setter.
- [x] **firmware-version feature gating** — documented in the README as a
  notice; intentionally not enforced in code.

## Integration polish (after protocol masks land)

- [x] Switches for per-port UFCS/PD/PPS enable (PIID 21 per-port toggles) and
  the global PIID 7 gates.
- [x] Switches for the `port_ctl` port-enable bitmap, after a deliberate Mi
  Home toggle capture confirms its writable bit mapping.
- [x] Select entity for `screen_save_time` (u8, piid `0x06`)

## Housekeeping

- [ ] Add screenshots to the root [README](README.md) (HACS landing page
  reads the root README).
- [ ] Drop a `.mailmap` only if GitHub's Contributors widget still shows
  `@claude` past 2026-04-25 — otherwise let it clear on its own.
