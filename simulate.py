#!/usr/bin/env python3
"""Replay a recorded session through the same logic the ESP32 runs.

    python simulate.py data_2026-08-29_evening_labeled.csv -o out/evening_decisions.csv
    python simulate.py data_2026-08-29_night_skin-only.csv --allow-missing-env -o out/night.csv

Input: a session CSV (t_s, skin_raw, env_temp, humidity[, label]) or a parsed
firmware log. Output: a CSV with the firmware log columns (+ boot, label),
ready for ``plot_night.py``.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flexrest_engine import LOG_COLUMNS  # noqa: E402
from flexrest_logio import firmware_style, read_session_csv, write_rows  # noqa: E402
from flexrest_replay import replay  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="session CSV with t_s, skin_raw, env_temp, humidity")
    ap.add_argument("-o", "--output", help="decisions CSV to write")
    ap.add_argument("--allow-missing-env", action="store_true",
                    help="do not FAULT when DHT11 data is missing (skin-only nights)")
    ap.add_argument("--gap-reset", type=float, default=10.0,
                    help="treat a gap longer than this (s) as a reboot (default 10)")
    ap.add_argument("--skin-baseline", type=float, default=None,
                    help="known personal skin_raw baseline (skips the 180 s calibration)")
    a = ap.parse_args(argv)

    samples = read_session_csv(a.csv)
    if not samples:
        print("no samples found", file=sys.stderr)
        return 1
    rows = replay(samples, allow_missing_env=a.allow_missing_env, gap_reset_s=a.gap_reset,
                  preset_skin_baseline=a.skin_baseline)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        write_rows([firmware_style(r) for r in rows], a.output, ["boot"] + LOG_COLUMNS + (["label"] if "label" in rows[0] else []))
        print(f"wrote {len(rows)} rows -> {a.output}")

    states = Counter(r["state"] for r in rows)
    transitions = sum(1 for x, y in zip(rows, rows[1:]) if x["state"] != y["state"])
    print(f"samples={len(rows)} segments={rows[-1]['boot'] + 1} transitions={transitions}")
    for s, n in states.most_common():
        print(f"  {s:<15} {n:>6} samples")
    print(f"  max pwm_out = {max(r['pwm_out'] for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
