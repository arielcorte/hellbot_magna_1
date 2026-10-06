"""Stream a G-code file to the printer over USB.

Each line goes out with a line number and checksum; the next one is sent only
after the printer answers "ok". If the stream is interrupted, the heaters are
turned off before exiting.

Usage: python scripts/print.py FILE.gcode [--port /dev/ttyUSB0]
"""

import argparse
import os
import select
import signal
import subprocess
import sys
import time

BAUD = 250000


class Port:
    """Minimal serial port. pyserial can't set 250000 baud here, stty can."""

    def __init__(self, path):
        subprocess.run(["stty", "-F", path, str(BAUD), "raw", "-echo", "-hupcl"], check=True)
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        self.buf = b""

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

    while True:
        resp = ser.readline().decode(errors="replace").strip()
        if not resp:
            continue
        if resp.startswith("ok"):
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
        if ser.readline().decode(errors="replace").strip().startswith("ok"):
            break
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
