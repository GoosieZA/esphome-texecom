# Texecom Premier 412 — Wintex/UDL serial protocol

Notes on the serial protocol used by this component, for anyone wanting to build
their own integration or extend this one. Everything here was reverse-engineered
against a **Premier 412, firmware v11.13** (`Premier 412 VB.0`). The Premier
"Elite" range is similar but differs in important places (see
[Gotchas](#gotchas--lessons-learned)).

## Physical layer

- The panel's onboard COM port (COM1, 5-pin Molex) must be set to **PC Com** in
  the Download/UDL programming menu (device type `0`).
- Serial: **19200 baud, 8 data bits, no parity, 2 stop bits (8N2)**.
- Logic level is **~5 V TTL**. Pin 1 is +12 V (do not connect to logic), pin 3
  is 0 V, pin 4 is the panel's RX, pin 5 is the panel's TX.

## Frame format

Every message, both directions:

```
[LENGTH] [COMMAND] [PAYLOAD...] [CHECKSUM]
```

- `LENGTH` — total byte count of the frame, **including** the length byte and
  the checksum.
- `COMMAND` — one byte (usually an ASCII letter).
- `CHECKSUM` — `(0xFF - (sum of every other byte)) & 0xFF`.

Example — the login "hello": `03 5A A2` (`0x03 + 0x5A = 0x5D`, `0xFF - 0x5D = 0xA2`).

## Commands

| Byte | ASCII | Direction | Meaning |
|------|-------|-----------|---------|
| `0x5A` | `Z` | → panel | Login / session |
| `0x4F` | `O` | → panel | Read **config** (EEPROM) memory |
| `0x49` | `I` | ← panel | Reply to a config read; also **write** config |
| `0x52` | `R` | → panel | Read **volatile** (live RAM) memory |
| `0x57` | `W` | ← panel / → panel | Reply to a volatile read; also **write** volatile |
| `0x41` | `A` | → panel | **Arm** areas (full / away) |
| `0x43` | `C` | → panel | **Reset** areas (clear a latched alarm) |
| `0x44` | `D` | → panel | **Disarm** areas |
| `0x53` | `S` | → panel | Part arm areas (from ESPHome_Wintex; untested here) |
| `0x4B` | `K` | → panel | Keypad keypress (untested here) |
| `0x06` | | ← panel | ACK (generic acknowledgement) |
| `0x0F` | | ← panel | NAK (rejected / not authorised) |

## Login

Two steps. **The passcode is sent as raw digit values, not ASCII** — this is the
single most important detail and the reason a naïve implementation fails (see
[Gotchas](#gotchas--lessons-learned)).

```
→  03 5A A2                          hello (empty session command)
←  0B 5A <6 bytes serial> <cksum>    panel serial number

→  07 5A 01 02 03 04 <cksum>         passcode "1234" as raw digits 01 02 03 04
←  13 5A "Premier 412 VB.0" <cksum>  banner => authenticated
```

If the passcode is wrong (or sent as ASCII `31 32 33 34`), the panel replies with
a bare ACK `03 06 F6` instead of the banner, and every subsequent command is
refused with NAK `03 0F ED`.

## Reading memory

Config and volatile reads share a format but use different commands and reply
with different command bytes:

```
Read config    →  07 4F <a2><a1><a0> <size> <cksum>   reply command 0x49 ('I')
Read volatile  →  07 52 <a2><a1><a0> <size> <cksum>   reply command 0x57 ('W')
```

Reply layout: `[LEN] [CMD] [a2] [a1] [a0] [size] [data...] [CKSUM]`. Address is
24-bit big-endian; `size` is a single byte (max 255). Accept any reply whose
command byte is not the `0x0F` NAK.

## Memory map (Premier 412 v11.13)

| Space | Address | Size | Contents |
|-------|---------|------|----------|
| config | `0x000000` | 16 bytes/zone | Zone **names** (zone 1 at `0x0000`, zone 2 at `0x0010`, …) |
| volatile | `0x0004F8` | 1 byte/zone | Live **zone status**, a bit field: `0x01` active/open, `0x02` tamper, `0x10` alarmed (latched until reset), `0x40` auto-bypassed |
| volatile | `0x0000E4` | 1 byte | **Arm state** (see heartbeat note): `0xE0`/`0x60` disarmed, `0x40` armed |
| volatile | `0x000558` | 1 byte | **Alarm active**: `0xFF` while triggered, `0x00` otherwise |
| volatile | `0x0005EC` | 1 byte | bit 6 (`0x40`) = **"an alarm occurred"** memory (does *not* block arming) |

The arm-state byte is **status only** — writing it does nothing. Use the arm/
disarm commands below.

**Bit 7 is a ~1 Hz heartbeat, not a state bit.** While disarmed the byte alternates
`0xE0` ⇄ `0x60` every poll (only bit 7 toggles); while armed it reads `0x40`. Compare
the byte with bit 7 masked off (`byte & 0x7F`: `0x60` = disarmed, `0x40` = armed) —
an exact `== 0xE0` test makes the state flap disarmed↔armed once per second.

**Triggered detection.** `0x000558` reads `0xFF` while the panel is in alarm and
`0x00` otherwise (confirmed by correlation over two separate triggers). Report
`triggered` as **armed AND `0x000558 == 0xFF`** — the byte lingers `0xFF` after a
disarm (alarm memory) and only resets to `0x00` on the next arm, so gate it on the
arm state or a stale alarm will show as triggered while re-armed. A separate
"an alarm occurred since last arm" memory bit is `0x0005EC` bit 6 (`0x40`); it is set
by an alarm and cleared at the keypad. That bit on its own does not block arming,
but the area's "reset required" latch does: see the reset note below.

## Arm / reset / disarm

Short commands with an **area bitmask** as the single payload byte (area 1 =
`0x01`, area 2 = `0x02`, area 3 = `0x04`, area 4 = `0x08`):

```
Arm area 1     →  04 41 01 <cksum>     ('A' + areas)
Reset area 1   →  04 43 01 <cksum>     ('C' + areas)
Disarm area 1  →  04 44 01 <cksum>     ('D' + areas)
```

The panel ACKs (`03 06 F6`) and the arm-state byte at `0x0000E4` changes
accordingly. No user code is needed — the UDL login already authenticated the
session.

**After an alarm, reset before arming.** Once an area has alarmed it stays
latched as "reset required": disarm works, but `A` is ACKed and silently
ignored until the area is reset. An RF fob still arms because it arms as a user,
which resets first. The component therefore always sends `C` then `A`.

## Writing memory

Not needed for zone reading or arm/disarm, but confirmed working:

```
Write config    →  <len> 49 <a2><a1><a0> <size> <data...> <cksum>
Write volatile  →  <len> 57 <a2><a1><a0> <size> <data...> <cksum>
```

For example, writing 16 bytes to config `0x0000` renames zone 1. Test writes on
an **unused** slot first and read them back — a stray write to live RAM can
disturb the panel.

## Gotchas & lessons learned

- **The passcode is raw digits, not ASCII.** `"1234"` → bytes `01 02 03 04`.
  This is the difference from the Elite range (which uses ASCII) and the reason
  the login appears to "succeed" (ACK) yet every read is refused. If you get a
  bare `03 06 F6` after the passcode instead of a model banner, this is why.
- **Read reply command bytes differ:** config reads reply with `0x49` ('I'),
  volatile reads reply with `0x57` ('W'). Don't assume a single "read response"
  code.
- **Arm/disarm is a command, not a memory write.** The Elite's "PC control
  latch" (write `0x3208`) does **not** work on the 412; the `A`/`D` commands do.
- **ComIP is a dead end for DIY.** On this firmware the COM port offers PC Com,
  RadioPlus, Inovonics and ComIP — no Crestron/Simple. ComIP uses an
  undocumented, NDA-protected `0x98 0xFE` module handshake; don't chase it. PC
  Com (this protocol) gives full read + control.
- **Level-shifter references matter.** A BSS138 shifter needs both `LV`→3.3 V and
  `HV`→5 V connected; miss one and the channels couple, looping TX back into RX
  (you'll see the panel "echo" your own bytes).
- **Avoid GPIO16/17 on WROVER ESP32 modules** — they're wired to PSRAM and
  self-echo the UART. GPIO25/26 are safe.
- The panel ACKs almost any well-formed frame with `0x06`, so an ACK does **not**
  mean the command did anything. Verify by reading state back. (Arm/disarm both
  ACK identically; watch `0x0000E4` to confirm the arm actually took — and note
  the panel won't arm while a zone is unsealed or a previous alarm is latched, so
  a clean/ready panel is required. A latched alarm is cleared with the `C`
  reset command.)
- **Zone status is a bit field, not a boolean.** Test bit 0 for "open"; the
  alarmed bit (`0x10`) stays set after an alarm, so a non-zero test reports the
  triggered zones as open until the panel is reset.
- **Areas are a bitmask in arm/reset/disarm.** Sending the raw area number only
  works for area 1 (area 3 as `0x03` means areas 1 *and* 2).
- **The arm-state byte's bit 7 is a heartbeat.** Mask it off (`& 0x7F`) before
  comparing, or the reported state flaps disarmed↔armed every poll. Reads should
  also be matched to the address the panel echoes back, not just to poll order —
  zone and arm reads share the same reply command byte, so a mis-paired reply can
  otherwise be decoded as the wrong field.
