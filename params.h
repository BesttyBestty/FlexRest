// Flex & Rest — tuning parameters (single source of truth for the firmware).
//
// The Python simulator mirrors these in flexrest_params.py;
// test_params_parity.py fails if the two drift apart.
// skin_raw is a raw 12-bit ADC trend value from the NTC divider, NOT degrees C.
#pragma once

namespace params {


// --- feature window and personal baseline ---
constexpr double WINDOW_S = 60.0;
constexpr int MIN_WINDOW_SAMPLES = 45;
constexpr double BASELINE_S = 180.0;

// --- Heat Score: normalisation (ADC falls when skin warms -> direction -1) ---
constexpr double SKIN_WARMING_DIRECTION = -1.0;
constexpr double SKIN_DELTA_SCALE_RAW = 35.0;
constexpr double SKIN_SLOPE_SCALE_RAW_PER_S = 0.3;
constexpr double ENV_REFERENCE_C = 28.0;
constexpr double ENV_LEVEL_SCALE_C = 4.0;
constexpr double ENV_SLOPE_SCALE_C_PER_S = 0.025;
constexpr double HUMIDITY_RELATIVE_SCALE = 0.1;
constexpr double HUMIDITY_SLOPE_SCALE_PCT_PER_S = 0.08;
constexpr double EVIDENCE_COMPONENT_THRESHOLD = 0.25;

// --- Heat Score weights (skin / bed temperature / bed humidity) ---
constexpr double W_SKIN_DELTA = 0.24;
constexpr double W_SKIN_SLOPE = 0.22;
constexpr double W_ENV_LEVEL = 0.12;
constexpr double W_ENV_SLOPE = 0.14;
constexpr double W_HUMIDITY_RELATIVE = 0.1;
constexpr double W_HUMIDITY_SLOPE = 0.13;
constexpr int PERSISTENCE_WINDOWS = 3;

// --- state machine thresholds; enter > exit gives hysteresis ---
constexpr double EARLY_ENTER_SCORE = 0.35;
constexpr double HIGH_ENTER_SCORE = 0.68;
constexpr double RECOVERY_ENTER_SCORE = 0.4;
constexpr double MAINTAIN_ENTER_SCORE = 0.22;
constexpr double STABLE_SCORE = 0.15;
constexpr double EARLY_MIN_TREND = 0.12;
constexpr double WORSENING_TREND = 0.25;
constexpr double RECOVERY_TREND = -0.12;
constexpr double STABLE_ABS_TREND = 0.1;
constexpr int EARLY_MIN_EVIDENCE = 2;
constexpr int HIGH_MIN_EVIDENCE = 3;
constexpr double EARLY_MIN_CONFIDENCE = 0.45;
constexpr double HIGH_MIN_CONFIDENCE = 0.6;
constexpr int EARLY_CONSECUTIVE = 3;
constexpr int HIGH_CONSECUTIVE = 2;
constexpr int RECOVERY_CONSECUTIVE = 3;
constexpr double MIN_HOLD_EARLY_S = 60.0;
constexpr double MIN_HOLD_HIGH_S = 90.0;
constexpr double MIN_HOLD_RECOVERY_S = 30.0;
constexpr double MIN_HOLD_MAINTAIN_S = 30.0;

// --- graded output, 8-bit PWM on the MOSFET gate ---
constexpr int EARLY_PWM = 48;
constexpr int HIGH_START_PWM = 112;
constexpr int MAINTAIN_PWM = 24;
constexpr int PWM_STEP = 24;
constexpr double POWER_STEP_S = 30.0;
constexpr double MAINTAIN_S = 60.0;

// --- actuator guard: max duty, max continuous on-time, cooldown ---
constexpr int MAX_PWM = 176;
constexpr double MAX_ON_S = 1800.0;
constexpr double COOLDOWN_S = 600.0;

// --- intervention response tracker ---
constexpr double RESP_MIN_OBSERVATION_S = 30.0;
constexpr double RESP_MIN_SCORE_CHANGE = 0.06;
constexpr double RESP_MIN_TREND = 0.1;
constexpr int RESP_IMPROVING_CONSECUTIVE = 2;
constexpr double RESP_RECOVERED_SCORE = 0.15;
constexpr int RESP_RECOVERED_CONSECUTIVE = 2;

// --- sensor plausibility, fail-closed ---
constexpr double SKIN_RAW_MIN = 50.0;
constexpr double SKIN_RAW_MAX = 4045.0;
constexpr double ENV_TEMP_MIN_C = -10.0;
constexpr double ENV_TEMP_MAX_C = 60.0;
constexpr double HUMIDITY_MIN_PCT = 0.0;
constexpr double HUMIDITY_MAX_PCT = 100.0;
constexpr int REQUIRE_ENVIRONMENT = 1;
constexpr double ENV_STALE_S = 10.0;

}  // namespace params
