"""Stream a G-code file to the printer over USB.

Each line goes out with a line number and checksum; the next one is sent only
after the printer answers "ok". If the stream is interrupted, the heaters are
turned off before exiting.

Usage: python scripts/print.py FILE.gcode [--port /dev/ttyUSB0]
"""

import argparse
import fcntl
import os
import re
import select
import signal
import struct
import sys
import termios
import time

BAUD = 250000

# Linux termios2 interface for non-standard baud rates (x86_64/arm64 values).
# pyserial fails at 250000 here and older stty versions reject it.
TCGETS2 = 0x802C542A
TCSETS2 = 0x402C542B
BOTHER = 0o010000
CBAUD = 0o010017
TERMIOS2 = "4IB19s2I"

# "ok" can arrive glued to another message (e.g. "echo:enok"), so don't
# require it at the start of the line.
OK = re.compile(r"^ok|ok( [TB]:|$)")
# If the printer says nothing for this long, assume a reply was lost and
# resend the current line. Marlin prints temperatures every second while
# heating, so silence this long means something went missing.
SILENCE_RESEND = 30


class Port:
    """Minimal raw serial port at BAUD."""

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        self.buf = b""
        buf = fcntl.ioctl(self.fd, TCGETS2, bytes(struct.calcsize(TERMIOS2)))
        _, _, cflag, _, line, cc, _, _ = struct.unpack(TERMIOS2, buf)
        # Raw 8N1, no hangup on close (so closing doesn't reset the board).
        cflag &= ~(CBAUD | termios.CSIZE | termios.PARENB | termios.CSTOPB
                   | termios.CRTSCTS | termios.HUPCL)
        cflag |= BOTHER | termios.CS8 | termios.CREAD | termios.CLOCAL
        cc = bytearray(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        fcntl.ioctl(self.fd, TCSETS2,
                    struct.pack(TERMIOS2, 0, 0, cflag, 0, line, bytes(cc), BAUD, BAUD))

    def write(self, data):
        os.write(self.fd, data)

    def readline(self, timeout=1.0):
        deadline = time.time() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.time()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                return b""
            self.buf += os.read(self.fd, 4096)
        line, self.buf = self.buf.split(b"\n", 1)
        return line

    def drain(self):
        while self.readline(2.0):
            pass


def checksum(line: str) -> int:
    c = 0
    for ch in line:
        c ^= ord(ch)
    return c


def gcode_lines(path):
    with open(path) as f:
        for raw in f:
            line = raw.split(";", 1)[0].strip()
            if line:
                yield line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    args = ap.parse_args()

    lines = list(gcode_lines(args.file))
    total = len(lines)

    ser = Port(args.port)
    # Opening the port resets the board; wait for Marlin to boot.
    time.sleep(6)
    ser.drain()

    def send(n, cmd):
        body = f"N{n} {cmd}"
        ser.write(f"{body}*{checksum(body)}\n".encode())

    def abort(*_):
        print("\nStopping: heaters off, motors off.", flush=True)
        ser.write(b"\nM104 S0\nM140 S0\nM107\nM84\n")
        sys.exit(1)

    signal.signal(signal.SIGINT, abort)
    signal.signal(signal.SIGTERM, abort)

    send(0, "M110 N0")
    sent = [None, *lines]  # sent[n] is the command sent as line n
    n = 0
    last_report = 0
    start = time.time()
    last_heard = time.time()

    while True:
        resp = ser.readline().decode(errors="replace").strip()
        if not resp:
            if time.time() - last_heard > SILENCE_RESEND:
                print(f"no reply for {SILENCE_RESEND}s, resending line {n}", flush=True)
                send(n, sent[n] if n else "M110 N0")
                last_heard = time.time()
            continue
        last_heard = time.time()
        if OK.search(resp):
            n += 1
            if n > total:
                break
            send(n, sent[n])
            pct = 100 * n // total
            if pct != last_report:
                last_report = pct
                mins = (time.time() - start) / 60
                print(f"{pct:3d}%  line {n}/{total}  {mins:.0f} min", flush=True)
        elif resp.startswith(("Resend:", "rs ")):
            n = int(resp.split(":")[-1].split()[-1]) - 1
            print(f"printer asked to resend line {n + 1}", flush=True)
        elif resp.startswith(("Error", "!!")) and "checksum" not in resp.lower() \
                and "line number" not in resp.lower():
            print(f"PRINTER ERROR: {resp}", flush=True)
            if "Printer halted" in resp or "Stopped" in resp:
                sys.exit(2)
        elif not resp.startswith(("echo:busy", "wait")):
            print(f"  {resp}", flush=True)

    # Wait for the last command to finish before closing the port.
    ser.write(b"M400\n")
    deadline = time.time() + 600
    while time.time() < deadline:
        if OK.search(ser.readline().decode(errors="replace").strip()):
            break
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
