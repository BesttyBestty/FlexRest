"""Replay the two real 2026-08-29 sessions; sanity checks only (no efficacy claims)."""
from flexrest_logio import read_session_csv
from flexrest_params import DEFAULT
from flexrest_replay import replay
from conftest import REPO

SESSIONS = REPO


def test_evening_session_reacts_to_warming_and_respects_limits():
    rows = replay(read_session_csv(SESSIONS / "data_2026-08-29_evening_labeled.csv"))
    assert len(rows) == 474
    states = {r["state"] for r in rows}
    assert "FAULT" not in states
    assert states & {"EARLY_HEAT", "HIGH_HEAT_LOAD"}
    assert max(r["pwm_out"] for r in rows) <= DEFAULT.max_pwm
    # first actuation happens only after the window has filled
    first_on = next(r["t_s"] for r in rows if r["pwm_out"] > 0)
    assert first_on >= DEFAULT.min_window_samples - 1


def test_skin_only_night_faults_by_default_and_runs_with_flag():
    samples = read_session_csv(SESSIONS / "data_2026-08-29_night_skin-only.csv")
    strict = replay(samples)
    assert all(r["pwm_out"] == 0 for r in strict)  # no DHT11 -> fail closed
    relaxed = replay(samples, allow_missing_env=True)
    assert all(r["state"] != "FAULT" for r in relaxed)
    # without bed evidence only one evidence group exists -> never reaches the 2-group gate
    assert all(r["evidence"] <= 1 for r in relaxed)
    assert all(r["pwm_out"] == 0 for r in relaxed)
