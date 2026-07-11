#!/usr/bin/env python3
"""txwintex - a small "Wintex for the Mac" for the Texecom Premier 412.

Speaks the panel's Wintex/UDL protocol over the COM port (PC-Com mode) and does
the everyday things Wintex does: log in, read the panel, list and rename zones,
watch live zone states, and dump the config - no Windows, no installer.

    pip install pyserial
    python3 tools/txwintex.py --port /dev/cu.usbserial-XXXX --code 1234 zones

Or over the ESP32 WiFi bridge (texecom-bridge.yaml):
    python3 tools/txwintex.py --socket 192.168.1.50:6638 --code 1234 zones

The crucial discovery for the International 412 firmware: the UDL passcode is
sent as RAW DIGIT VALUES, not ASCII - "1234" -> bytes 01 02 03 04. That's what
unlocks the session (a real login returns the panel banner, e.g. "Premier 412
VB.0"). Serial is 19200 8N2.
"""

import argparse
import socket
import sys
import time

try:
    import serial
except ImportError:
    serial = None

# ---- protocol ------------------------------------------------------------
CMD_LOGIN = 0x5A          # 'Z'
CMD_READ_CONFIG = 0x4F    # 'O'  read config/EEPROM  -> reply 'I' (0x49)
CMD_READ_VOLATILE = 0x52  # 'R'  read live/RAM
CMD_RESP = 0x49           # 'I'
CMD_NAK = 0x0F

# Known memory layout (this panel / firmware).
ZONE_NAME_BASE = 0x0000   # config: 16 bytes per zone name
ZONE_NAME_LEN = 16
ZONE_STATUS_BASE = 0x04F8  # volatile: 1 byte per zone, 0x00 secure / 0x01 active
ARM_STATE_ADDRESS = 0x00E4  # volatile: 0xE0 disarmed / 0x40 armed
DISARMED_VALUE = 0xE0
MAX_ZONES = 16


def checksum(body: bytes) -> int:
    return (0xFF - (sum(body) & 0xFF)) & 0xFF


def frame(command: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload) + 3, command]) + payload
    return body + bytes([checksum(body)])


def encode_passcode(code: str) -> bytes:
    # RAW digit values, not ASCII. "1234" -> 01 02 03 04.
    return bytes(int(c) for c in code)


def addr3(a: int) -> bytes:
    return bytes([(a >> 16) & 0xFF, (a >> 8) & 0xFF, a & 0xFF])


# ---- transport (serial or TCP) ------------------------------------------
class SerialTransport:
    def __init__(self, port: str, baud: int = 19200):
        if serial is None:
            sys.exit("pyserial required: pip install pyserial")
        self.ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=2, timeout=0.4)

    def reset(self):
        self.ser.reset_input_buffer()

    def write(self, data: bytes):
        self.ser.write(data)
        self.ser.flush()

    def read(self, n: int) -> bytes:
        return self.ser.read(n)


class SocketTransport:
    def __init__(self, hostport: str):
        host, port = hostport.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=3)
        self.sock.settimeout(0.6)

    def reset(self):
        self.sock.setblocking(False)
        try:
            while self.sock.recv(256):
                pass
        except (BlockingIOError, OSError):
            pass
        self.sock.settimeout(0.6)

    def write(self, data: bytes):
        self.sock.sendall(data)

    def read(self, n: int) -> bytes:
        try:
            return self.sock.recv(n)
        except (socket.timeout, BlockingIOError):
            return b""


# ---- panel ---------------------------------------------------------------
class Panel:
    def __init__(self, transport, verbose: bool = False):
        self.t = transport
        self.verbose = verbose

    def _command(self, command: int, payload: bytes = b"", wait: float = 0.3) -> bytes:
        self.t.reset()
        pkt = frame(command, payload)
        self.t.write(pkt)
        if self.verbose:
            print("  TX:", pkt.hex(" "))
        time.sleep(wait)
        reply = self.t.read(255)
        if self.verbose:
            print("  RX:", reply.hex(" "))
        return reply

    def login(self, code: str) -> str:
        self._command(CMD_LOGIN, wait=0.25)                       # hello -> serial
        reply = self._command(CMD_LOGIN, encode_passcode(code))   # passcode -> banner
        if not reply or len(reply) <= 3 or reply[1] != CMD_LOGIN:
            raise RuntimeError(
                "login failed - the panel did not return a banner. Check the UDL "
                "passcode, that Com1 is set to PC-Com, and that the digits are right."
            )
        return bytes(reply[2:-1]).decode("latin1").strip()

    def _read(self, command: int, addr: int, size: int) -> bytes:
        out = bytearray()
        while size > 0:
            chunk = min(size, 240)
            reply = self._command(command, addr3(addr) + bytes([chunk]))
            # Config reads reply with 'I' (0x49), volatile reads with 'W' (0x57);
            # anything short or 0x0F is a refusal.
            if not reply or len(reply) < 7 or reply[1] == CMD_NAK:
                raise RuntimeError(f"read @0x{addr:06X} refused: {reply.hex() if reply else 'no reply'}")
            data = bytes(reply[6:-1])
            out += data
            addr += chunk
            size -= chunk
        return bytes(out)

    def read_config(self, addr: int, size: int) -> bytes:
        return self._read(CMD_READ_CONFIG, addr, size)

    def read_volatile(self, addr: int, size: int) -> bytes:
        return self._read(CMD_READ_VOLATILE, addr, size)

    # ---- higher level ----
    def zone_name(self, zone: int) -> str:
        raw = self.read_config(ZONE_NAME_BASE + (zone - 1) * ZONE_NAME_LEN, ZONE_NAME_LEN)
        return raw.decode("latin1").strip()

    def present_zones(self):
        block = self.read_config(ZONE_NAME_BASE, MAX_ZONES * ZONE_NAME_LEN)
        zones = []
        for z in range(MAX_ZONES):
            name = block[z * ZONE_NAME_LEN:(z + 1) * ZONE_NAME_LEN].decode("latin1").strip()
            if name:
                zones.append((z + 1, name))
        return zones

    def zone_states(self):
        return self.read_volatile(ZONE_STATUS_BASE, MAX_ZONES)

    def write_config(self, command: int, addr: int, data: bytes) -> bytes:
        return self._command(command, addr3(addr) + bytes([len(data)]) + data)

    def arm(self, area: int = 1):
        self._command(0x41, bytes([area]))   # 'A' + area

    def disarm(self, area: int = 1):
        self._command(0x44, bytes([area]))   # 'D' + area

    def arm_state(self) -> int:
        d = self.read_volatile(ARM_STATE_ADDRESS, 1)
        return d[0] if d else -1


def state_text(b: int) -> str:
    return {0x00: "secure", 0x01: "ACTIVE"}.get(b, f"0x{b:02X}")


# ---- CLI -----------------------------------------------------------------
def build_panel(args) -> Panel:
    if args.socket:
        t = SocketTransport(args.socket)
    elif args.port:
        t = SerialTransport(args.port)
    else:
        sys.exit("give --port /dev/cu.usbserial-XXXX  or  --socket host:6638")
    return Panel(t, verbose=args.verbose)


def cmd_info(p, args):
    print("Panel:", p.login(args.code))


def cmd_zones(p, args):
    print("Panel:", p.login(args.code))
    a = p.arm_state()
    print("System:", "DISARMED" if a == DISARMED_VALUE else f"ARMED (0x{a:02X})")
    zones = p.present_zones()
    states = p.zone_states()
    print(f"\n{'Zn':>3}  {'Name':<18} State")
    print("-" * 34)
    for num, name in zones:
        print(f"{num:>3}  {name:<18} {state_text(states[num - 1])}")


def cmd_arm(p, args):
    print("Panel:", p.login(args.code))
    p.arm(args.area)
    print(f"armed area {args.area}")


def cmd_disarm(p, args):
    print("Panel:", p.login(args.code))
    p.disarm(args.area)
    print(f"disarmed area {args.area}")


def cmd_watch(p, args):
    print("Panel:", p.login(args.code))
    zones = p.present_zones()
    names = {n: nm for n, nm in zones}
    print("Live zone view (Ctrl-C to stop):")
    last = None
    try:
        while True:
            st = p.zone_states()
            if st != last:
                active = [f"{n}:{names[n]}" for n, _ in zones if st[n - 1] != 0]
                ts = time.strftime("%H:%M:%S")
                print(f"  [{ts}] active: {', '.join(active) if active else 'none (all secure)'}")
                last = st
            time.sleep(0.4)
    except KeyboardInterrupt:
        print()


def cmd_read(p, args):
    p.login(args.code)
    data = p.read_config(int(args.addr, 16), args.size)
    _hexdump(int(args.addr, 16), data)


def cmd_readv(p, args):
    p.login(args.code)
    data = p.read_volatile(int(args.addr, 16), args.size)
    _hexdump(int(args.addr, 16), data)


def cmd_dump(p, args):
    p.login(args.code)
    start, end = int(args.start, 16), int(args.end, 16)
    data = p.read_config(start, end - start)
    with open(args.file, "wb") as f:
        f.write(data)
    print(f"wrote {len(data)} bytes (0x{start:06X}..0x{end:06X}) to {args.file}")


def cmd_rename(p, args):
    print("Panel:", p.login(args.code))
    if args.write_cmd is None:
        sys.exit(
            "Renaming needs the confirmed config-write command for this firmware.\n"
            "Run `calibrate-write` once at the panel first, then pass --write-cmd 0xNN."
        )
    zone = args.zone
    before = p.zone_name(zone)
    name = args.name[:ZONE_NAME_LEN].ljust(ZONE_NAME_LEN)
    p.write_config(int(args.write_cmd, 16), ZONE_NAME_BASE + (zone - 1) * ZONE_NAME_LEN, name.encode("latin1"))
    time.sleep(0.3)
    after = p.zone_name(zone)
    if after == args.name.strip():
        print(f"zone {zone}: {before!r} -> {after!r}  OK")
    else:
        print(f"zone {zone}: write did NOT stick (still {after!r}). Wrong write command?")


def cmd_calibrate_write(p, args):
    """Safely find the config-write command by writing to an UNUSED zone-name slot."""
    print("Panel:", p.login(args.code))
    # find an empty zone slot to experiment on (won't touch real zones)
    zones = dict(p.present_zones())
    target = next((z for z in range(1, MAX_ZONES + 1) if z not in zones), None)
    if target is None:
        sys.exit("no empty zone slot to test on safely; add --slot N for a spare one")
    addr = ZONE_NAME_BASE + (target - 1) * ZONE_NAME_LEN
    print(f"testing writes on empty zone {target} (config 0x{addr:06X})...")
    marker = b"TXWTEST".ljust(ZONE_NAME_LEN)
    for cmd in (0x57, 0x4F, 0x50, 0x77, 0x6F, 0x53, 0x49):  # candidate write commands
        p.write_config(cmd, addr, marker)
        time.sleep(0.3)
        got = p.zone_name(target)
        ok = got == "TXWTEST"
        print(f"  cmd 0x{cmd:02X} ({chr(cmd)}) -> {'*** WRITES! ***' if ok else 'no change'}")
        if ok:
            # restore blank
            p.write_config(cmd, addr, b"\x00" * ZONE_NAME_LEN)
            print(f"\nConfig-write command is 0x{cmd:02X}. Use: --write-cmd 0x{cmd:02X}")
            return
    print("\nNone of the candidates wrote. We'll need to widen the search together.")


def cmd_shell(p, args):
    print("Panel:", p.login(args.code))
    print("commands: zones | arm [area] | disarm [area] | read <hex> <n> | readv <hex> <n> | name <z> | quit")
    while True:
        try:
            line = input("txwintex> ").strip().split()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not line:
            continue
        try:
            c = line[0]
            if c in ("quit", "exit"):
                return
            elif c == "zones":
                st = p.zone_states()
                for n, nm in p.present_zones():
                    print(f"  {n:>2} {nm:<18} {state_text(st[n-1])}")
            elif c == "read":
                _hexdump(int(line[1], 16), p.read_config(int(line[1], 16), int(line[2])))
            elif c == "readv":
                _hexdump(int(line[1], 16), p.read_volatile(int(line[1], 16), int(line[2])))
            elif c == "name":
                print("  ", p.zone_name(int(line[1])))
            elif c == "arm":
                p.arm(int(line[1]) if len(line) > 1 else 1); print("  armed")
            elif c == "disarm":
                p.disarm(int(line[1]) if len(line) > 1 else 1); print("  disarmed")
            else:
                print("  ?")
        except Exception as e:
            print("  error:", e)


def _hexdump(base: int, data: bytes):
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        hx = " ".join(f"{b:02X}" for b in row)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"  0x{base+i:06X}  {hx:<48}  |{asc}|")


def main():
    ap = argparse.ArgumentParser(description="Wintex-lite for the Texecom Premier 412 (Mac/Linux)")
    ap.add_argument("--port", help="serial device, e.g. /dev/cu.usbserial-XXXX")
    ap.add_argument("--socket", help="host:port of the ESP32 bridge, e.g. 192.168.1.50:6638")
    ap.add_argument("--code", default="1234", help="UDL passcode (digits)")
    ap.add_argument("--verbose", action="store_true", help="show TX/RX hex")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info")
    sub.add_parser("zones")
    sub.add_parser("watch")
    sub.add_parser("shell")
    sub.add_parser("calibrate-write")
    am = sub.add_parser("arm"); am.add_argument("--area", type=int, default=1)
    dm = sub.add_parser("disarm"); dm.add_argument("--area", type=int, default=1)
    r = sub.add_parser("read"); r.add_argument("addr"); r.add_argument("size", type=int)
    rv = sub.add_parser("readv"); rv.add_argument("addr"); rv.add_argument("size", type=int)
    d = sub.add_parser("dump"); d.add_argument("start"); d.add_argument("end"); d.add_argument("file")
    rn = sub.add_parser("rename")
    rn.add_argument("zone", type=int); rn.add_argument("name")
    rn.add_argument("--write-cmd", help="config-write command byte, e.g. 0x57 (from calibrate-write)")

    args = ap.parse_args()
    p = build_panel(args)
    handlers = {
        "info": cmd_info, "zones": cmd_zones, "watch": cmd_watch, "shell": cmd_shell,
        "read": cmd_read, "readv": cmd_readv, "dump": cmd_dump, "rename": cmd_rename,
        "calibrate-write": cmd_calibrate_write, "arm": cmd_arm, "disarm": cmd_disarm,
    }
    handlers[args.cmd](p, args)


if __name__ == "__main__":
    main()
