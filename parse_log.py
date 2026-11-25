#!/usr/bin/env python3
"""Clean a raw firmware serial capture into a tidy CSV.

    python parse_log.py capture.txt -o out/night_log.csv

Comment lines (``#``), repeated headers after reboots and truncated lines are
handled; a ``boot`` column counts reboots.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flexrest_engine import LOG_COLUMNS  # noqa: E402
from flexrest_logio import parse_firmware_log, write_rows  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="raw serial capture (text)")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    rows = parse_firmware_log(Path(a.log).read_text(errors="replace"))
    if not rows:
        print("no firmware log lines found", file=sys.stderr)
        return 1
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    write_rows(rows, a.output, ["boot"] + LOG_COLUMNS)
    print(f"{len(rows)} rows, {rows[-1]['boot'] + 1} boot(s) -> {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
