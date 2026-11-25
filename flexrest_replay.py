"""Replay a recorded night through the firmware-mirror engine."""
from __future__ import annotations

from dataclasses import replace

from flexrest_engine import Engine
from flexrest_params import DEFAULT, Params


def replay(samples: list[dict], params: Params = DEFAULT, allow_missing_env: bool = False,
           gap_reset_s: float = 10.0, preset_skin_baseline: float | None = None) -> list[dict]:
    """Feed samples (dicts with t_s, skin_raw, env_temp, humidity) through the engine.

    A gap longer than ``gap_reset_s`` is treated like a device reboot: the
    engine (window, baseline, state machine, guard) is re-initialised, exactly
    as the firmware would after a power cycle.

    Note: this is open-loop. The recorded data was not influenced by the
    simulated output, so the replay shows *what the controller would have
    decided*, not what the actuator would have achieved.
    """
    p = replace(params, require_environment=0) if allow_missing_env else params
    out: list[dict] = []
    engine: Engine | None = None
    last_t = None
    segment = -1
    for s in samples:
        t = float(s["t_s"])
        if engine is None or last_t is None or t - last_t > gap_reset_s:
            engine = Engine(p, preset_skin_baseline, now=t)
            segment += 1
        last_t = t
        rec = engine.step(t, float(s["skin_raw"]), s.get("env_temp"), s.get("humidity")).as_dict()
        rec["boot"] = segment
        if "label" in s:
            rec["label"] = s["label"]
        out.append(rec)
    return out
