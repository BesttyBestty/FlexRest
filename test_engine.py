import math

from flexrest_engine import (ActuatorGuard, Assessment, Engine, FeatureWindow, ResponseTracker,
                             StateMachine, check_sample, score, Features)
from flexrest_params import DEFAULT
from flexrest_replay import replay
from synth import HEAT_EPISODE, night

P = DEFAULT


# ---------------------------------------------------------------- safety
def test_safety_rejects_open_ntc_and_missing_env():
    assert check_sample(P, 4090, 26, 50) == (False, "ntc_out_of_range")
    assert check_sample(P, 10, 26, 50) == (False, "ntc_out_of_range")
    assert check_sample(P, 600, float("nan"), 50) == (False, "env_sensor_missing")
    assert check_sample(P, 600, 80, 50) == (False, "env_temp_abnormal")
    assert check_sample(P, 600, 26, 120) == (False, "humidity_abnormal")
    assert check_sample(P, 600, 26, 50) == (True, "ok")


# ---------------------------------------------------------------- window / baseline
def test_baseline_ready_after_180s_and_delta_signed():
    w = FeatureWindow(P)
    for t in range(0, 181):
        w.add(float(t), 600.0, 26.0, 50.0)
    assert w.skin_baseline == 600.0
    for t in range(181, 241):
        w.add(float(t), 580.0, 26.0, 50.0)
    f = w.features()
    assert f is not None and f.skin_delta < 0  # lower ADC == warmer than baseline


def test_no_features_before_min_samples():
    w = FeatureWindow(P)
    for t in range(P.min_window_samples - 1):
        w.add(float(t), 600.0, 26.0, 50.0)
    assert w.features() is None


def test_slope_is_least_squares_per_second():
    w = FeatureWindow(P)
    for t in range(60):
        w.add(float(t), 600.0 - 0.5 * t, 26.0 + 0.01 * t, 50.0)
    f = w.features()
    assert math.isclose(f.skin_slope, -0.5, abs_tol=1e-9)
    assert math.isclose(f.env_slope, 0.01, abs_tol=1e-9)


# ---------------------------------------------------------------- Heat Score
def _feat(**kw):
    base = dict(skin_mean=600, skin_slope=0.0, skin_delta=0.0, env_mean=28.0, env_slope=0.0,
                hum_mean=55.0, hum_slope=0.0, hum_relative=0.0)
    base.update(kw)
    return Features(**base)


def test_heat_score_neutral_is_zero():
    a = score(P, _feat(), 1.0)
    assert a.heat_score == 0 and a.evidence == 0


def test_heat_score_rises_with_multi_evidence_and_bounds():
    a = score(P, _feat(skin_delta=-70, skin_slope=-0.6, env_mean=34, env_slope=0.05,
                       hum_relative=0.3, hum_slope=0.2), 1.0)
    assert a.heat_score == 1.0 and a.evidence == 3 and a.trend_score == 1.0
    assert 0.0 <= a.confidence <= 1.0


def test_single_skin_spike_is_one_evidence_only():
    a = score(P, _feat(skin_slope=-0.6), 1.0)
    assert a.evidence == 1 and a.heat_score < P.early_enter_score


def test_missing_sensor_is_removed_not_zero_and_lowers_confidence():
    full = score(P, _feat(skin_delta=-35, skin_slope=-0.3), 1.0)
    no_env = score(P, _feat(skin_delta=-35, skin_slope=-0.3, env_mean=None, env_slope=None,
                            hum_mean=None, hum_slope=None, hum_relative=None), 1.0)
    assert no_env.heat_score > full.heat_score  # skin evidence not diluted by "zeros"
    assert no_env.confidence < full.confidence


def test_no_baseline_lowers_confidence():
    a = score(P, _feat(skin_slope=-0.3), 1.0)
    b = score(P, _feat(skin_slope=-0.3, skin_delta=None), 1.0)
    assert b.confidence < a.confidence


# ---------------------------------------------------------------- state machine
def _a(heat, trend, ev=3, conf=0.9):
    return Assessment(heat, trend, conf, ev, True)


def test_early_needs_consecutive_windows():
    sm = StateMachine(P, 0)
    for t in range(P.early_consecutive - 1):
        assert sm.update(_a(0.4, 0.3, ev=2), True, "ok", t).state == "STABLE"
    d = sm.update(_a(0.4, 0.3, ev=2), True, "ok", 10)
    assert d.state == "EARLY_HEAT" and d.pwm == P.early_pwm


def test_low_confidence_or_single_evidence_never_starts():
    sm = StateMachine(P, 0)
    for t in range(20):
        assert sm.update(_a(0.9, 0.9, ev=1), True, "ok", t).pwm == 0
        assert sm.update(_a(0.9, 0.9, ev=3, conf=0.3), True, "ok", t).pwm == 0


def test_high_direct_and_min_hold_before_recovery():
    sm = StateMachine(P, 0)
    sm.update(_a(0.8, 0.5), True, "ok", 0)
    assert sm.update(_a(0.8, 0.5), True, "ok", 1).state == "HIGH_HEAT_LOAD"
    # improving immediately, but HIGH must be held for min_hold_high_s
    for t in range(2, 60):
        assert sm.update(_a(0.2, -0.3), True, "ok", t).state == "HIGH_HEAT_LOAD"
    states = [sm.update(_a(0.2, -0.3), True, "ok", t).state for t in range(91, 95)]
    assert "RECOVERY" in states


def test_hysteresis_no_chatter_around_early_threshold():
    """Score oscillating just around the entry threshold must not toggle output each second."""
    sm = StateMachine(P, 0)
    pwms = []
    for t in range(600):
        heat = 0.36 if t % 2 else 0.34
        pwms.append(sm.update(_a(heat, 0.2, ev=2), True, "ok", t).pwm)
    toggles = sum(1 for a, b in zip(pwms, pwms[1:]) if (a > 0) != (b > 0))
    assert toggles == 0


def test_full_cycle_returns_to_stable():
    sm = StateMachine(P, 0)
    t = 0
    seen = []
    for _ in range(5):
        seen.append(sm.update(_a(0.5, 0.3, ev=2), True, "ok", t).state); t += 1
    for _ in range(200):
        seen.append(sm.update(_a(0.3, -0.2, ev=1), True, "ok", t).state); t += 1
    for _ in range(300):
        seen.append(sm.update(_a(0.05, 0.0, ev=0), True, "ok", t).state); t += 1
    order = [s for i, s in enumerate(seen) if i == 0 or seen[i - 1] != s]
    assert order == ["STABLE", "EARLY_HEAT", "RECOVERY", "MAINTAIN", "STABLE"]


def test_fault_latches_until_reset():
    sm = StateMachine(P, 0)
    d = sm.update(_a(0.0, 0.0), False, "env_sensor_missing", 0)
    assert d.state == "FAULT" and d.pwm == 0
    assert sm.update(_a(0.9, 0.9), True, "ok", 1).state == "FAULT"
    assert sm.reset_fault(2)
    assert sm.state == "STABLE"


# ---------------------------------------------------------------- actuator guard
def test_guard_clamps_duty():
    g = ActuatorGuard(P)
    assert g.apply(255, 0) == (P.max_pwm, "clamped_max_duty")


def test_guard_max_on_time_then_cooldown():
    g = ActuatorGuard(P)
    t = 0.0
    while t < P.max_on_s:
        out, why = g.apply(100, t)
        assert out == 100
        t += 1
    assert g.apply(100, t) == (0, "max_on_time_cooldown")
    assert g.apply(100, t + P.cooldown_s - 1) == (0, "cooldown")
    assert g.apply(100, t + P.cooldown_s)[0] == 100


# ---------------------------------------------------------------- response tracker
def test_response_waits_then_improves():
    r = ResponseTracker(P)
    assert r.update(0.7, 0.3, 112, "HIGH_HEAT_LOAD", 0) == "WAITING_FOR_RESPONSE"
    assert r.update(0.6, -0.2, 112, "HIGH_HEAT_LOAD", 10) == "WAITING_FOR_RESPONSE"
    r.update(0.5, -0.2, 112, "HIGH_HEAT_LOAD", 31)
    assert r.update(0.45, -0.2, 112, "HIGH_HEAT_LOAD", 32) == "IMPROVING"
    assert r.update(0.1, 0.0, 0, "STABLE", 40) == "RECOVERED"


# ---------------------------------------------------------------- end to end
def test_synthetic_heat_episode_full_loop():
    rows = replay(night(HEAT_EPISODE))
    states = [r["state"] for r in rows]
    order = [s for i, s in enumerate(states) if i == 0 or states[i - 1] != s]
    assert order[0] == "STABLE" and order[-1] == "STABLE"
    assert "EARLY_HEAT" in order and "HIGH_HEAT_LOAD" in order and "RECOVERY" in order
    assert "FAULT" not in order
    assert max(r["pwm_out"] for r in rows) <= P.max_pwm
    # calm baseline period never actuates
    assert all(r["pwm_out"] == 0 for r in rows if r["t_s"] < 240)


def test_dht_dropout_faults_and_turns_off():
    rows = night(HEAT_EPISODE)
    for r in rows[400:]:
        r["env_temp"] = None
        r["humidity"] = None
    out = replay(rows)
    assert out[400]["state"] == "FAULT" and out[400]["pwm_out"] == 0
    assert all(r["pwm_out"] == 0 for r in out[400:])


def test_engine_stop_latches():
    e = Engine(P)
    e.stop(0)
    rec = e.step(1.0, 600, 26, 50)
    assert rec.state == "FAULT" and rec.pwm_out == 0 and rec.reason == "manual_stop"
