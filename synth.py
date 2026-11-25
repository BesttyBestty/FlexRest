"""Deterministic synthetic nights for tests (no randomness)."""
from __future__ import annotations


def night(phases: list[tuple[float, float, float, float]], start_skin: float = 600.0,
          start_env: float = 26.5, start_hum: float = 55.0) -> list[dict]:
    """phases: (duration_s, skin_raw_per_s, env_c_per_s, rh_pct_per_s) at 1 Hz.

    skin_raw falling == skin warming (NTC divider orientation).
    """
    rows, t, skin, env, hum = [], 0.0, start_skin, start_env, start_hum
    for duration, ds, de, dh in phases:
        for _ in range(int(duration)):
            rows.append({"t_s": t, "skin_raw": round(skin, 3), "env_temp": round(env, 3),
                         "humidity": round(hum, 3)})
            t += 1.0
            skin += ds
            env += de
            hum += dh
    return rows


# calm 4 min (baseline), warming 3 min, plateau 4 min, cooling back 6 min, calm 6 min
HEAT_EPISODE = [
    (240, 0.0, 0.0, 0.0),
    (180, -0.45, 0.02, 0.08),
    (240, 0.0, 0.0, 0.0),
    (360, 0.225, -0.01, -0.04),
    (360, 0.0, 0.0, 0.0),
]
