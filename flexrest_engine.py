"""Python mirror of the on-device logic in ``thermal_core.cpp``.

One ``Engine.step()`` call == one 1 Hz loop iteration on the ESP32:

    sample -> safety check -> 60 s window + personal baseline
           -> Heat Score / Trend / Confidence -> state machine
           -> actuator guard -> intervention response

The arithmetic is written in the same order as the C++ code so that a
replayed CSV gives the same states on both sides (see
``tests/test_firmware_parity.py``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
import math

from flexrest_params import DEFAULT, Params

STABLE = "STABLE"
EARLY_HEAT = "EARLY_HEAT"
HIGH_HEAT_LOAD = "HIGH_HEAT_LOAD"
RECOVERY = "RECOVERY"
MAINTAIN = "MAINTAIN"
FAULT = "FAULT"
ACTIVE_STATES = {EARLY_HEAT, HIGH_HEAT_LOAD, RECOVERY, MAINTAIN}

LOG_COLUMNS = [
    "t_s", "skin_raw", "env_temp", "humidity", "baseline_ready", "skin_baseline",
    "heat_score", "trend_score", "confidence", "evidence", "state", "action",
    "reason", "pwm_req", "pwm_out", "guard", "response",
]


def _finite(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------
def check_sample(p: Params, skin: float, env: float, hum: float) -> tuple[bool, str]:
    if not _finite(skin) or skin < p.skin_raw_min or skin > p.skin_raw_max:
        return False, "ntc_out_of_range"
    env_ok, hum_ok = _finite(env), _finite(hum)
    if env_ok and (env < p.env_temp_min_c or env > p.env_temp_max_c):
        return False, "env_temp_abnormal"
    if hum_ok and (hum < p.humidity_min_pct or hum > p.humidity_max_pct):
        return False, "humidity_abnormal"
    if p.require_environment and (not env_ok or not hum_ok):
        return False, "env_sensor_missing"
    return True, "ok"


# --------------------------------------------------------------------------
# Feature window + personal baseline
# --------------------------------------------------------------------------
def _mean_slope(ts: list[float], ys: list[float]) -> tuple[float | None, float | None]:
    """Mean and least-squares slope over finite points (slope in units/s)."""
    n = 0
    sum_t = sum_y = 0.0
    for t, y in zip(ts, ys):
        if _finite(y):
            n += 1
            sum_t += t
            sum_y += y
    if n == 0:
        return None, None
    mt, my = sum_t / n, sum_y / n
    if n < 2:
        return my, None
    sxy = sxx = 0.0
    for t, y in zip(ts, ys):
        if _finite(y):
            dt = t - mt
            sxy += dt * (y - my)
            sxx += dt * dt
    if sxx <= 0.0:
        return my, None
    return my, sxy / sxx


@dataclass
class Features:
    skin_mean: float
    skin_slope: float | None
    skin_delta: float | None
    env_mean: float | None
    env_slope: float | None
    hum_mean: float | None
    hum_slope: float | None
    hum_relative: float | None


class FeatureWindow:
    def __init__(self, p: Params = DEFAULT, preset_skin_baseline: float | None = None):
        self.p = p
        self.buf: deque[tuple[float, float, float, float]] = deque()
        self.t0: float | None = None
        self.skin_sum = 0.0
        self.skin_n = 0
        self.hum_sum = 0.0
        self.hum_n = 0
        self.preset = preset_skin_baseline
        self.ready = False

    def restart_baseline(self) -> None:
        self.t0 = None
        self.skin_sum = self.hum_sum = 0.0
        self.skin_n = self.hum_n = 0
        self.ready = False

    @property
    def skin_baseline(self) -> float | None:
        if self.preset is not None:
            return self.preset
        return self.skin_sum / self.skin_n if self.ready and self.skin_n else None

    @property
    def hum_baseline(self) -> float | None:
        return self.hum_sum / self.hum_n if self.ready and self.hum_n else None

    def add(self, t: float, skin: float, env: float, hum: float) -> None:
        if self.t0 is None:
            self.t0 = t
        if not self.ready:
            if t - self.t0 <= self.p.baseline_s:
                self.skin_sum += skin
                self.skin_n += 1
                if _finite(hum):
                    self.hum_sum += hum
                    self.hum_n += 1
            if t - self.t0 >= self.p.baseline_s and self.skin_n > 0:
                self.ready = True
        self.buf.append((t, skin, env, hum))
        while self.buf and t - self.buf[0][0] > self.p.window_s:
            self.buf.popleft()

    def features(self) -> Features | None:
        if len(self.buf) < self.p.min_window_samples:
            return None
        t_start = self.buf[0][0]
        ts = [b[0] - t_start for b in self.buf]
        skin_mean, skin_slope = _mean_slope(ts, [b[1] for b in self.buf])
        env_mean, env_slope = _mean_slope(ts, [b[2] for b in self.buf])
        hum_mean, hum_slope = _mean_slope(ts, [b[3] for b in self.buf])
        sb, hb = self.skin_baseline, self.hum_baseline
        skin_delta = skin_mean - sb if sb is not None else None
        hum_rel = None
        if hum_mean is not None and hb is not None:
            hum_rel = (hum_mean - hb) / max(abs(hb), 1.0)
        return Features(skin_mean, skin_slope, skin_delta, env_mean, env_slope,
                        hum_mean, hum_slope, hum_rel)


# --------------------------------------------------------------------------
# Heat Score
# --------------------------------------------------------------------------
@dataclass
class Assessment:
    heat_score: float = 0.0
    trend_score: float = 0.0
    confidence: float = 0.0
    evidence: int = 0
    baseline_ready: bool = False


def score(p: Params, f: Features, persistence: float) -> Assessment:
    d = p.skin_warming_direction
    # signed evidence, normalised so that 1.0 == "clearly warming"
    s_skin_delta = None if f.skin_delta is None else d * f.skin_delta / p.skin_delta_scale_raw
    s_skin_slope = None if f.skin_slope is None else d * f.skin_slope / p.skin_slope_scale_raw_per_s
    s_env_level = None if f.env_mean is None else (f.env_mean - p.env_reference_c) / p.env_level_scale_c
    s_env_slope = None if f.env_slope is None else f.env_slope / p.env_slope_scale_c_per_s
    s_hum_rel = None if f.hum_relative is None else f.hum_relative / p.humidity_relative_scale
    s_hum_slope = None if f.hum_slope is None else f.hum_slope / p.humidity_slope_scale_pct_per_s

    comps = [  # (signed, weight, group, is_trend)
        (s_skin_delta, p.w_skin_delta, 0, False),
        (s_skin_slope, p.w_skin_slope, 0, True),
        (s_env_level, p.w_env_level, 1, False),
        (s_env_slope, p.w_env_slope, 1, True),
        (s_hum_rel, p.w_humidity_relative, 2, False),
        (s_hum_slope, p.w_humidity_slope, 2, True),
    ]
    num = den = tnum = tden = all_w = 0.0
    group_max = [-1.0, -1.0, -1.0]  # -1 == group missing
    for signed, w, g, is_trend in comps:
        all_w += w
        if signed is None:
            continue
        c = _clip(signed)
        num += c * w
        den += w
        if c > group_max[g]:
            group_max[g] = c
        if is_trend:
            tnum += _clip(signed, -1.0, 1.0) * w
            tden += w
    heat = _clip(num / den) if den > 0 else 0.0
    trend = tnum / tden if tden > 0 else 0.0
    evidence = sum(1 for m in group_max if m >= p.evidence_component_threshold)

    coverage = den / all_w if all_w > 0 else 0.0
    agreement = 0.35 if evidence == 0 else min(1.0, 0.45 + 0.275 * evidence)
    baseline_factor = 1.0 if f.skin_delta is not None else 0.8
    conf = _clip(coverage * (0.45 + 0.30 * _clip(persistence) + 0.25 * agreement) * baseline_factor)
    return Assessment(heat, trend, conf, evidence, f.skin_delta is not None)


# --------------------------------------------------------------------------
# State machine (graded, with hysteresis and minimum hold times)
# --------------------------------------------------------------------------
@dataclass
class Decision:
    state: str
    pwm: int
    action: str
    reason: str


class StateMachine:
    def __init__(self, p: Params = DEFAULT, now: float = 0.0):
        self.p = p
        self.state = STABLE
        self.pwm = 0
        self.entered = now
        self.last_power_change = now
        self.n_early = self.n_high = self.n_recovery = 0
        self.fault_reason = ""

    def _min_hold(self) -> float:
        p = self.p
        return {EARLY_HEAT: p.min_hold_early_s, HIGH_HEAT_LOAD: p.min_hold_high_s,
                RECOVERY: p.min_hold_recovery_s, MAINTAIN: p.min_hold_maintain_s}.get(self.state, 0.0)

    def _held(self, now: float) -> bool:
        return now - self.entered >= self._min_hold()

    def _limit(self, pwm: int) -> int:
        return max(0, min(int(pwm), self.p.max_pwm))

    def _go(self, state: str, pwm: int, action: str, reason: str, now: float) -> Decision:
        self.state, self.pwm, self.entered = state, self._limit(pwm), now
        self.last_power_change = now
        self.n_early = self.n_high = self.n_recovery = 0
        return Decision(self.state, self.pwm, action, reason)

    def fault(self, reason: str, now: float) -> Decision:
        if self.state == FAULT:
            return Decision(FAULT, 0, "SAFETY_STOP", self.fault_reason)
        self.fault_reason = reason
        return self._go(FAULT, 0, "SAFETY_STOP", reason, now)

    def reset_fault(self, now: float) -> bool:
        if self.state != FAULT:
            return False
        self.fault_reason = ""
        self._go(STABLE, 0, "RESET_FAULT", "manual_reset", now)
        return True

    def update(self, a: Assessment, safe: bool, safety_reason: str, now: float) -> Decision:
        p = self.p
        if self.state == FAULT:
            return Decision(FAULT, 0, "SAFETY_STOP", self.fault_reason)
        if not safe:
            return self.fault(safety_reason, now)

        high = (a.heat_score >= p.high_enter_score and a.evidence >= p.high_min_evidence
                and a.confidence >= p.high_min_confidence)
        early = (a.heat_score >= p.early_enter_score and a.trend_score >= p.early_min_trend
                 and a.evidence >= p.early_min_evidence and a.confidence >= p.early_min_confidence)
        improving = a.trend_score <= p.recovery_trend or a.heat_score <= p.recovery_enter_score
        stable = a.heat_score <= p.stable_score and abs(a.trend_score) <= p.stable_abs_trend
        self.n_high = self.n_high + 1 if high else 0
        self.n_early = self.n_early + 1 if early else 0
        self.n_recovery = self.n_recovery + 1 if improving else 0
        high_ready = self.n_high >= p.high_consecutive
        early_ready = self.n_early >= p.early_consecutive
        recovery_ready = self.n_recovery >= p.recovery_consecutive
        since_power = now - self.last_power_change

        s = self.state
        if s == STABLE:
            if high_ready:
                return self._go(HIGH_HEAT_LOAD, p.high_start_pwm, "DIRECT_HIGH", "high_score_multi_evidence", now)
            if early_ready:
                return self._go(EARLY_HEAT, p.early_pwm, "START_LOW", "persistent_early_heat_trend", now)
            return Decision(STABLE, 0, "HOLD_OFF", "stable_or_unconfirmed")

        if s == EARLY_HEAT:
            if high_ready:
                return self._go(HIGH_HEAT_LOAD, p.high_start_pwm, "ESCALATE", "heat_load_became_high", now)
            if recovery_ready and self._held(now):
                return self._go(RECOVERY, self.pwm, "BEGIN_RAMP_DOWN", "trend_improving", now)
            if early and since_power >= p.power_step_s:
                self.pwm = self._limit(min(self.pwm + p.pwm_step, p.high_start_pwm))
                self.last_power_change = now
                return Decision(s, self.pwm, "STEP_UP", "early_heat_still_rising")

        elif s == HIGH_HEAT_LOAD:
            if recovery_ready and self._held(now):
                return self._go(RECOVERY, self.pwm, "BEGIN_RAMP_DOWN", "heat_trend_converging", now)
            if high and a.trend_score > p.worsening_trend and since_power >= p.power_step_s:
                self.pwm = self._limit(self.pwm + p.pwm_step)
                self.last_power_change = now
                return Decision(s, self.pwm, "STEP_UP", "high_heat_still_rising")

        elif s == RECOVERY:
            if high_ready:
                return self._go(HIGH_HEAT_LOAD, p.high_start_pwm, "REESCALATE", "high_heat_returned", now)
            if early_ready:
                return self._go(EARLY_HEAT, max(p.early_pwm, self.pwm), "REESCALATE", "early_heat_returned", now)
            if improving and since_power >= p.power_step_s:
                self.pwm = self._limit(max(p.maintain_pwm, self.pwm - p.pwm_step))
                self.last_power_change = now
                if self.pwm <= p.maintain_pwm and a.heat_score <= p.maintain_enter_score:
                    return self._go(MAINTAIN, p.maintain_pwm, "LIGHT_MAINTAIN", "recovery_near_stable", now)
                return Decision(s, self.pwm, "STEP_DOWN", "improving_lower_power")

        elif s == MAINTAIN:
            if high_ready:
                return self._go(HIGH_HEAT_LOAD, p.high_start_pwm, "REESCALATE", "high_heat_returned", now)
            if early_ready:
                return self._go(EARLY_HEAT, p.early_pwm, "REESCALATE", "early_heat_returned", now)
            if stable and self._held(now) and now - self.entered >= p.maintain_s:
                return self._go(STABLE, 0, "TURN_OFF", "stable_after_maintain", now)

        return Decision(self.state, self.pwm, "HOLD", "min_hold_or_no_condition")


# --------------------------------------------------------------------------
# Actuator guard: max duty, max continuous on-time, forced cooldown
# --------------------------------------------------------------------------
class ActuatorGuard:
    def __init__(self, p: Params = DEFAULT):
        self.p = p
        self.on_since: float | None = None
        self.cooldown_until: float | None = None
        self.out = 0

    def apply(self, requested: int, now: float) -> tuple[int, str]:
        p = self.p
        if requested <= 0:
            self.out, self.on_since = 0, None
            return 0, "off"
        if self.cooldown_until is not None:
            if now < self.cooldown_until:
                self.out, self.on_since = 0, None
                return 0, "cooldown"
            self.cooldown_until = None
        out, guard = requested, "ok"
        if out > p.max_pwm:
            out, guard = p.max_pwm, "clamped_max_duty"
        if self.on_since is None:
            self.on_since = now
        if now - self.on_since >= p.max_on_s:
            self.cooldown_until = now + p.cooldown_s
            self.out, self.on_since = 0, None
            return 0, "max_on_time_cooldown"
        self.out = out
        return out, guard


# --------------------------------------------------------------------------
# Did the intervention help?
# --------------------------------------------------------------------------
class ResponseTracker:
    def __init__(self, p: Params = DEFAULT):
        self.p = p
        self.started: float | None = None
        self.ref_score = 0.0
        self.last_pwm = 0
        self.n_improving = self.n_recovered = 0

    def update(self, heat: float, trend: float, pwm: int, state: str, now: float) -> str:
        p = self.p
        if pwm <= 0:
            recovered = self.started is not None and state == STABLE
            self.started, self.last_pwm = None, 0
            self.n_improving = self.n_recovered = 0
            return "RECOVERED" if recovered else "NOT_INTERVENING"
        if self.started is None or pwm != self.last_pwm:
            self.started, self.ref_score, self.last_pwm = now, heat, pwm
            self.n_improving = self.n_recovered = 0
            return "WAITING_FOR_RESPONSE"
        elapsed = now - self.started
        change = heat - self.ref_score
        if elapsed < p.resp_min_observation_s:
            return "WAITING_FOR_RESPONSE"
        if heat <= p.resp_recovered_score and abs(trend) <= p.stable_abs_trend:
            self.n_recovered += 1
            if self.n_recovered >= p.resp_recovered_consecutive:
                return "RECOVERED"
        else:
            self.n_recovered = 0
        if change <= -p.resp_min_score_change or trend <= -p.resp_min_trend:
            self.n_improving += 1
            if self.n_improving >= p.resp_improving_consecutive:
                return "IMPROVING"
        else:
            self.n_improving = 0
        if change >= p.resp_min_score_change and trend >= p.resp_min_trend:
            return "WORSENING"
        return "NO_CLEAR_RESPONSE"


# --------------------------------------------------------------------------
# Whole pipeline
# --------------------------------------------------------------------------
@dataclass
class Record:
    t_s: float
    skin_raw: float
    env_temp: float | None
    humidity: float | None
    baseline_ready: int
    skin_baseline: float | None
    heat_score: float
    trend_score: float
    confidence: float
    evidence: int
    state: str
    action: str
    reason: str
    pwm_req: int
    pwm_out: int
    guard: str
    response: str

    def as_dict(self) -> dict:
        return asdict(self)


class Engine:
    def __init__(self, p: Params = DEFAULT, preset_skin_baseline: float | None = None, now: float = 0.0):
        self.p = p
        self.window = FeatureWindow(p, preset_skin_baseline)
        self.sm = StateMachine(p, now)
        self.guard = ActuatorGuard(p)
        self.resp = ResponseTracker(p)
        self.heat_run = 0

    def stop(self, now: float) -> None:
        """Serial ``STOP``: latch a fault so the output stays off until RESET."""
        self.sm.fault("manual_stop", now)

    def reset(self, now: float) -> bool:
        return self.sm.reset_fault(now)

    def step(self, t: float, skin: float, env: float | None, hum: float | None) -> Record:
        p = self.p
        env = env if _finite(env) else float("nan")
        hum = hum if _finite(hum) else float("nan")
        safe, why = check_sample(p, skin, env, hum)
        a = Assessment()
        if _finite(skin):
            self.window.add(t, skin, env, hum)
            f = self.window.features()
            if f is not None:
                a = score(p, f, 0.0)
                self.heat_run = min(self.heat_run + 1, p.persistence_windows) \
                    if a.heat_score >= p.early_enter_score else 0
                a = score(p, f, self.heat_run / p.persistence_windows)
        a.baseline_ready = self.window.skin_baseline is not None
        d = self.sm.update(a, safe, why, t)
        if d.state == FAULT:
            out, g = self.guard.apply(0, t)
            g = "fault_off"
        else:
            out, g = self.guard.apply(d.pwm, t)
        r = self.resp.update(a.heat_score, a.trend_score, out, d.state, t)
        return Record(t, skin, None if math.isnan(env) else env, None if math.isnan(hum) else hum,
                      int(a.baseline_ready), self.window.skin_baseline, a.heat_score, a.trend_score,
                      a.confidence, a.evidence, d.state, d.action, d.reason, d.pwm, out, g, r)
