#!/usr/bin/env python3
"""Plot one night: skin trend, bed microclimate, Heat Score + state, output.

    python plot_night.py out/evening_decisions.csv -o evening.png
    python plot_night.py data_2026-08-29_night_skin-only.csv -o night.png

Accepts a decisions CSV (``simulate.py`` output or ``parse_log.py`` output).
A plain session CSV is plotted too (sensor panels only).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

STATE_COLORS = {
    "STABLE": "#e8eef4",
    "EARLY_HEAT": "#fde2b8",
    "HIGH_HEAT_LOAD": "#f6b1a6",
    "RECOVERY": "#c9e6d2",
    "MAINTAIN": "#d9d4ee",
    "FAULT": "#9e9e9e",
}


def _f(v: str | None):
    try:
        return float(v) if v not in (None, "") else float("nan")
    except ValueError:
        return float("nan")


def load(path: str) -> dict[str, list]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    cols: dict[str, list] = {k: [r.get(k) for r in rows] for k in (rows[0].keys() if rows else [])}
    return cols


def _spans(t: list[float], states: list[str]):
    """Contiguous (start, end, state) runs, broken at gaps > 10 s."""
    if not t:
        return
    start, cur = t[0], states[0]
    for i in range(1, len(t)):
        if states[i] != cur or t[i] - t[i - 1] > 10:
            yield start, t[i - 1], cur
            start, cur = t[i], states[i]
    yield start, t[-1], cur


def plot(path: str, output: str, title: str | None = None) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    c = load(path)
    t_s = [_f(v) for v in c["t_s"]]
    span = (t_s[-1] - t_s[0]) if t_s else 0
    scale, unit = (3600.0, "h") if span > 3 * 3600 else (60.0, "min")
    t = [(v - t_s[0]) / scale for v in t_s]
    gaps = {i for i in range(1, len(t_s)) if t_s[i] - t_s[i - 1] > 10}

    def broken(values: list[float]) -> list[float]:
        """NaN at recording gaps so lines are not drawn across them."""
        return [float("nan") if i in gaps else v for i, v in enumerate(values)]
    skin = [_f(v) for v in c["skin_raw"]]
    env = [_f(v) for v in c.get("env_temp", [])]
    hum = [_f(v) for v in c.get("humidity", [])]
    has_decisions = "heat_score" in c
    has_env = any(v == v for v in env)
    label = [_f(v) for v in c["label"]] if "label" in c else None
    if label and not any(v == 1 for v in label):
        label = None

    n_panels = 1 + (1 if has_env else 0) + (2 if has_decisions else 0)
    fig, axes = plt.subplots(n_panels, 1, figsize=(11, 2.3 * n_panels + 0.6), sharex=True,
                             gridspec_kw={"hspace": 0.12})
    axes = list(axes) if n_panels > 1 else [axes]
    ax_i = 0

    def shade(ax):
        if has_decisions:
            for a, b, s in _spans(t, c["state"]):
                ax.axvspan(a, b if b > a else a + 1 / scale, color=STATE_COLORS.get(s, "#fff"), lw=0, zorder=0)
        if label:
            for i in range(1, len(t)):
                if label[i] == 1:
                    ax.axvspan(t[i - 1], t[i], ymin=0.94, ymax=1.0, color="#3b6fb6", lw=0, zorder=1)

    ax = axes[ax_i]; ax_i += 1
    shade(ax)
    ax.plot(t, broken(skin), color="#7a3e9d", lw=1.0, label="skin_raw (NTC ADC)")
    if has_decisions and "skin_baseline" in c:
        base = [_f(v) for v in c["skin_baseline"]]
        ax.plot(t, broken(base), color="#7a3e9d", lw=1.0, ls="--", alpha=0.6, label="personal baseline")
    ax.invert_yaxis()  # ADC falls when skin warms -> up == warmer
    ax.set_ylabel("skin_raw\n(up = warmer)")
    ax.legend(loc="upper left", fontsize=8, frameon=False)

    if has_env:
        ax = axes[ax_i]; ax_i += 1
        shade(ax)
        ax.plot(t, broken(env), color="#c0392b", lw=1.0, label="bed temp (°C)")
        ax.set_ylabel("bed °C")
        ax2 = ax.twinx()
        ax2.plot(t, broken(hum), color="#2471a3", lw=1.0, label="bed RH (%)")
        ax2.set_ylabel("RH %")
        h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, frameon=False)

    if has_decisions:
        ax = axes[ax_i]; ax_i += 1
        shade(ax)
        ax.plot(t, broken([_f(v) for v in c["heat_score"]]), color="#d35400", lw=1.2, label="Heat Score")
        ax.plot(t, broken([_f(v) for v in c["trend_score"]]), color="#555", lw=0.8, alpha=0.7, label="Trend")
        ax.plot(t, broken([_f(v) for v in c["confidence"]]), color="#16a085", lw=0.8, ls=":", label="Confidence")
        for y, name in ((0.35, "early"), (0.68, "high")):
            ax.axhline(y, color="#d35400", lw=0.6, ls="--", alpha=0.6)
            ax.text(t[-1], y, f" {name}", va="center", fontsize=7, color="#d35400")
        ax.set_ylim(-1.05, 1.05)
        ax.set_ylabel("score")
        ax.legend(loc="lower left", fontsize=8, frameon=False, ncol=3)

        ax = axes[ax_i]; ax_i += 1
        shade(ax)
        ax.step(t, [_f(v) for v in c["pwm_req"]], where="post", color="#999", lw=0.8, label="requested")
        ax.step(t, [_f(v) for v in c["pwm_out"]], where="post", color="#1f2d3d", lw=1.2, label="after guard")
        ax.axhline(176, color="#c0392b", lw=0.6, ls="--")
        ax.text(t[-1], 176, " max duty", va="center", fontsize=7, color="#c0392b")
        ax.set_ylim(-5, 200)
        ax.set_ylabel("TEC PWM\n(simulated)")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        present = set(c["state"])
        handles = [Patch(color=v, label=k) for k, v in STATE_COLORS.items() if k in present]
        if label:
            handles.append(Patch(color="#3b6fb6", label="self-report: wants cooler"))
        fig.legend(handles=handles, loc="lower center", ncol=len(handles), fontsize=8, frameon=False)

    axes[-1].set_xlabel(f"time ({unit})")
    fig.suptitle(title or Path(path).stem, fontsize=11)
    fig.subplots_adjust(bottom=0.1 if has_decisions else 0.12, top=0.94)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--title")
    a = ap.parse_args(argv)
    plot(a.csv, a.output, a.title)
    print(f"saved {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
