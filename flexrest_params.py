"""Tuning parameters shared with the firmware.

Every name here must match a ``constexpr`` in ``params.h``
(upper-cased). ``test_params_parity.py`` enforces this, so the
simulator and the ESP32 always run the same numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Params:
    # --- feature window and personal baseline ---
    window_s: float = 60.0               # rolling window for mean / slope
    min_window_samples: int = 45         # fewer samples -> no assessment yet
    baseline_s: float = 180.0            # first 3 min of the night = personal baseline

    # --- Heat Score: normalisation of each piece of evidence ---
    skin_warming_direction: float = -1.0  # NTC divider: ADC falls when skin warms
    skin_delta_scale_raw: float = 35.0
    skin_slope_scale_raw_per_s: float = 0.30
    env_reference_c: float = 28.0
    env_level_scale_c: float = 4.0
    env_slope_scale_c_per_s: float = 0.025
    humidity_relative_scale: float = 0.10
    humidity_slope_scale_pct_per_s: float = 0.08
    evidence_component_threshold: float = 0.25

    # --- Heat Score weights ---
    w_skin_delta: float = 0.24
    w_skin_slope: float = 0.22
    w_env_level: float = 0.12
    w_env_slope: float = 0.14
    w_humidity_relative: float = 0.10
    w_humidity_slope: float = 0.13
    persistence_windows: int = 3

    # --- state machine: entry/exit thresholds (hysteresis) ---
    early_enter_score: float = 0.35
    high_enter_score: float = 0.68
    recovery_enter_score: float = 0.40
    maintain_enter_score: float = 0.22
    stable_score: float = 0.15
    early_min_trend: float = 0.12
    worsening_trend: float = 0.25
    recovery_trend: float = -0.12
    stable_abs_trend: float = 0.10
    early_min_evidence: int = 2
    high_min_evidence: int = 3
    early_min_confidence: float = 0.45
    high_min_confidence: float = 0.60
    early_consecutive: int = 3
    high_consecutive: int = 2
    recovery_consecutive: int = 3
    min_hold_early_s: float = 60.0
    min_hold_high_s: float = 90.0
    min_hold_recovery_s: float = 30.0
    min_hold_maintain_s: float = 30.0

    # --- graded output (8-bit PWM to the MOSFET gate) ---
    early_pwm: int = 48
    high_start_pwm: int = 112
    maintain_pwm: int = 24
    pwm_step: int = 24
    power_step_s: float = 30.0
    maintain_s: float = 60.0

    # --- actuator guard (last line of software protection) ---
    max_pwm: int = 176                   # ~69 % duty ceiling
    max_on_s: float = 1800.0             # continuous on-time limit
    cooldown_s: float = 600.0            # forced off-time after the limit

    # --- intervention response tracker ---
    resp_min_observation_s: float = 30.0
    resp_min_score_change: float = 0.06
    resp_min_trend: float = 0.10
    resp_improving_consecutive: int = 2
    resp_recovered_score: float = 0.15
    resp_recovered_consecutive: int = 2

    # --- sensor plausibility / fail-closed ---
    skin_raw_min: float = 50.0           # below -> NTC shorted / disconnected
    skin_raw_max: float = 4045.0         # above -> NTC open
    env_temp_min_c: float = -10.0
    env_temp_max_c: float = 60.0
    humidity_min_pct: float = 0.0
    humidity_max_pct: float = 100.0
    require_environment: int = 1         # missing DHT11 data -> FAULT
    env_stale_s: float = 10.0

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


DEFAULT = Params()
