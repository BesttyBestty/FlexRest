#!/usr/bin/env python3
"""Save the firmware's serial output to a text file (one night = one file).

    python capture_serial.py --port /dev/cu.usbmodemXXXX -o captures/night.txt

Type a command (STOP / RESET / BASELINE / HELP) and press Enter to send it.
Ctrl-C stops. Requires ``pyserial``.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    import serial  # imported lazily so tests don't need pyserial

    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    with serial.Serial(a.port, a.baud, timeout=1) as ser, open(a.output, "a", buffering=1) as fh:
        def forward_commands() -> None:
            for line in sys.stdin:
                ser.write((line.strip() + "\n").encode())

        threading.Thread(target=forward_commands, daemon=True).start()
        print(f"capturing {a.port} -> {a.output} (Ctrl-C to stop)")
        try:
            while True:
                line = ser.readline().decode(errors="replace").rstrip()
                if line:
                    fh.write(line + "\n")
                    print(line)
        except KeyboardInterrupt:
            ser.write(b"STOP\n")  # leave the actuator off when logging ends
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
