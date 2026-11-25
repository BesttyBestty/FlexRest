"""Read session CSVs and firmware serial logs into one tidy format."""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path

from flexrest_engine import LOG_COLUMNS

NUMERIC = {"t_s", "skin_raw", "env_temp", "humidity", "skin_baseline", "heat_score",
           "trend_score", "confidence"}
INTEGER = {"baseline_ready", "evidence", "pwm_req", "pwm_out", "label"}


def _num(value: str, integer: bool = False):
    value = (value or "").strip()
    if value == "":
        return None
    try:
        x = float(value)
    except ValueError:
        return None
    if not math.isfinite(x):
        return None
    return int(x) if integer else x


def parse_firmware_log(text: str) -> list[dict]:
    """Parse raw serial output from the firmware.

    * lines starting with ``#`` are comments / command echoes
    * the header is repeated after every reboot -> a new ``boot`` index starts
    * truncated or garbled lines (wrong column count, bad numbers) are skipped
    """
    rows: list[dict] = []
    header = ",".join(LOG_COLUMNS)
    boot = -1
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == header:
            boot += 1
            continue
        if boot < 0:
            continue  # data before the first header (partial boot output)
        parts = next(csv.reader(io.StringIO(line)))
        if len(parts) != len(LOG_COLUMNS):
            continue
        row = dict(zip(LOG_COLUMNS, parts))
        out: dict = {"boot": boot}
        ok = True
        for k, v in row.items():
            if k in NUMERIC:
                out[k] = _num(v)
            elif k in INTEGER:
                out[k] = _num(v, integer=True)
            else:
                out[k] = v
        if out["t_s"] is None or out["skin_raw"] is None:
            ok = False
        if ok:
            rows.append(out)
    return rows


def read_session_csv(path: str | Path) -> list[dict]:
    """Read ``data_*.csv`` (t_s, skin_raw, env_temp, humidity, label)."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            t, skin = _num(r.get("t_s")), _num(r.get("skin_raw"))
            if t is None or skin is None:
                continue
            rows.append({"t_s": t, "skin_raw": skin, "env_temp": _num(r.get("env_temp")),
                         "humidity": _num(r.get("humidity")), "label": _num(r.get("label"), True)})
    return rows


_ROUND = {"t_s": 3, "skin_raw": 0, "env_temp": 1, "humidity": 1, "skin_baseline": 2,
          "heat_score": 4, "trend_score": 4, "confidence": 4}


def firmware_style(row: dict) -> dict:
    """Round a record the way the firmware prints it (same CSV on both sides)."""
    out = dict(row)
    for k, nd in _ROUND.items():
        v = out.get(k)
        if isinstance(v, float):
            out[k] = int(round(v)) if nd == 0 else f"{v:.{nd}f}"
    return out


def write_rows(rows: list[dict], path: str | Path, columns: list[str] | None = None) -> None:
    columns = columns or list(rows[0].keys()) if rows else (columns or LOG_COLUMNS)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r.get(k) for k in columns})
