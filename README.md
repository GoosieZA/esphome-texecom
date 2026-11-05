# ESPHome Texecom Premier

An [ESPHome](https://esphome.io) external component for **Texecom Premier
"International"** alarm panels (412 / 816 / 832). It connects to the panel's
serial COM port and exposes:

- every **zone** as a `binary_sensor` (live),
- the system as an **`alarm_control_panel`** you can arm and disarm from Home
  Assistant.

No cloud, no subscription, no proprietary module — it speaks the panel's
Wintex/UDL protocol directly over the COM port.

> **Tested on:** Texecom **Premier 412**, firmware **v11.13** (panel identifies
> itself as `Premier 412 VB.0`). The 816/832 and other International-series
> firmwares share this protocol and are very likely compatible, but are
> untested — feedback welcome.

> ⚠️ **Disclaimer.** This is an independent, reverse-engineered integration and
> is not affiliated with or endorsed by Texecom. Working on an alarm panel
> touches life-safety wiring and may void your monitoring/insurance agreement.
> If the system is professionally monitored, clear this with your installer
> first. Use at your own risk.

## Features

| | |
|---|---|
| Zones | live `binary_sensor` per zone |
| Arm state | reflected in the `alarm_control_panel` (and updates when armed at a keypad) |
| Arm / disarm | Arm Away and Disarm from Home Assistant, per area (arming also clears a latched alarm) |
| Connection | direct serial, or over WiFi via the optional [ESP bridge](texecom-bridge.yaml) |

## Hardware

You need an **ESP32** (or ESP8266) and a **logic level shifter**. The Premier
COM port carries **12 V** and **~5 V TTL** serial, so the panel's TX must not be
wired directly to a 3.3 V ESP pin.

### Panel COM1 connector (5-pin Molex, 5 V TTL)

| Pin | Function | Wire to |
|-----|----------|---------|
| 1 | +12 V | leave unconnected (power the ESP separately) |
| 2 | not connected | — |
| 3 | 0 V | common ground (ESP GND + shifter GND) |
| 4 | Panel RX | shifter → ESP **TX** |
| 5 | Panel TX | shifter → ESP **RX** |

### Wiring (ESP32 ↔ BSS138 4-channel shifter ↔ panel)

```
   LV side (3.3 V / ESP)            HV side (5 V / panel)
   ─────────────────────           ─────────────────────
   LV1 ← ESP GPIO25 (TX)   ══ch1══  HV1 → panel pin 4 (Panel RX)
   LV2 ← ESP GPIO26 (RX)   ══ch2══  HV2 → panel pin 5 (Panel TX)
   LV  ← ESP 3V3                    HV  → 5 V
   GND ← ESP GND                    GND → panel pin 3 (0 V)
```

**Both reference pins are required** — `LV`→3.3 V *and* `HV`→5 V — otherwise the
shifter channels couple and TX loops back to RX. Identify the COM pins with a
multimeter first (pin 1 reads ~12 V; pin 5 idles ~5 V). GPIO25/26 are used
because GPIO16/17 are tied to PSRAM on WROVER modules.

## Panel setup

Set the wired COM port to the **PC Com** protocol, and note the panel's **UDL
passcode**:

1. Enter engineer mode.
2. Set **Com1 Device Type = `0` (PC Com)** (in the Download/UDL programming menu).
3. Note the **UDL / Download passcode** (plain digits — the component sends it).
4. Exit engineer mode.

## Installation

```yaml
external_components:
  - source: github://GoosieZA/esphome-texecom
    components: [texecom]
```

Then configure the UART and the `texecom` platforms — see
[`texecom-premier.yaml`](texecom-premier.yaml) for a complete example. Copy
[`secrets.yaml.example`](secrets.yaml.example) to `secrets.yaml` and fill it in.

## Configuration

### `texecom` (hub)

```yaml
uart:
  id: texecom_uart
  tx_pin: GPIO25
  rx_pin: GPIO26
  baud_rate: 19200
  data_bits: 8
  parity: NONE
  stop_bits: 2

texecom:
  id: panel
  uart_id: texecom_uart
  udl: !secret texecom_udl_passcode   # UDL passcode, plain digits (required)
```

| Option | Default | Description |
|--------|---------|-------------|
| `udl` | — | UDL/Download passcode, plain digits. Required. |
| `zone_status_address` | `0x0004F8` | Live zone-status base address (see [PROTOCOL](docs/PROTOCOL.md)). |
| `zone_count` | `16` | Number of zone-status bytes read per poll. |
| `arm_state_address` | `0x0000E4` | Arm-state byte address. |
| `disarmed_value` | `0xE0` | Value of the arm-state byte when disarmed. |
| `poll_interval` | `1s` | How often to refresh zone/arm state. |

### `alarm_control_panel`

```yaml
alarm_control_panel:
  - platform: texecom
    texecom_id: panel
    name: "Alarm"
    area: 1        # panel area to arm/disarm
```

Supports **Arm Away** and **Disarm**. No code is required in Home Assistant (the
UDL passcode already authenticates the connection).

### `binary_sensor`

One entry per zone (`zone: N`), or the system arm state (`type: armed`). All
standard binary-sensor options apply.

```yaml
binary_sensor:
  - platform: texecom
    texecom_id: panel
    zone: 1
    name: "Front Door"
    device_class: door

  - platform: texecom     # optional: plain armed/disarmed sensor
    texecom_id: panel
    type: armed
    name: "Alarm Armed"
    device_class: safety
```

## Command-line tool

[`tools/txwintex.py`](tools/txwintex.py) is a small "Wintex for the terminal"
(pyserial) — handy for setup and debugging without Home Assistant:

```bash
pip install pyserial
python3 tools/txwintex.py --port /dev/tty.usbserial-XXXX --code 1234 zones
python3 tools/txwintex.py --port /dev/tty.usbserial-XXXX --code 1234 arm
python3 tools/txwintex.py --socket <esp-ip>:6638 --code 1234 watch   # via the ESP bridge
```

It can list zones + live state, arm/disarm, read/dump memory, and run an
interactive shell. Run with no subcommand for help.

## Protocol

The full serial protocol — framing, login, memory map, arm/disarm commands, and
the reverse-engineering notes (including the gotchas that make the International
series different from the Elite) — is documented in
**[docs/PROTOCOL.md](docs/PROTOCOL.md)**.

## Credits

Protocol pieced together with help from the community's prior work:
[pytexalarm](https://github.com/shuckc/pytexalarm),
[ESPHome_Wintex](https://github.com/RoganDawes/ESPHome_Wintex), and
[cybergibbons](https://cybergibbons.com/alarms-2/programming-a-texecom-premier-elite-12-w-using-a-ftdi-cable/).
The WiFi bridge uses [oxan/esphome-stream-server](https://github.com/oxan/esphome-stream-server).

## License

[MIT](LICENSE).
