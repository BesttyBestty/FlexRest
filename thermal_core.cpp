// Flex & Rest — hardware-independent thermal-state core. See thermal_core.h.
#include "thermal_core.h"

#include <cstdio>

#include "params.h"

namespace flexrest {

using namespace params;

namespace {
inline bool finite(double x) { return std::isfinite(x); }
inline double clip(double x, double lo = 0.0, double hi = 1.0) { return x < lo ? lo : (x > hi ? hi : x); }
inline int imax(int a, int b) { return a > b ? a : b; }
inline int imin(int a, int b) { return a < b ? a : b; }
}  // namespace

const char* stateName(State s) {
  switch (s) {
    case State::STABLE: return "STABLE";
    case State::EARLY_HEAT: return "EARLY_HEAT";
    case State::HIGH_HEAT_LOAD: return "HIGH_HEAT_LOAD";
    case State::RECOVERY: return "RECOVERY";
    case State::MAINTAIN: return "MAINTAIN";
    case State::FAULT: return "FAULT";
  }
  return "?";
}

const char* responseName(Response r) {
  switch (r) {
    case Response::NOT_INTERVENING: return "NOT_INTERVENING";
    case Response::WAITING_FOR_RESPONSE: return "WAITING_FOR_RESPONSE";
    case Response::IMPROVING: return "IMPROVING";
    case Response::NO_CLEAR_RESPONSE: return "NO_CLEAR_RESPONSE";
    case Response::WORSENING: return "WORSENING";
    case Response::RECOVERED: return "RECOVERED";
  }
  return "?";
}

// ============================================================== safety
bool checkSample(const Sample& s, bool requireEnvironment, const char** reason) {
  if (!finite(s.skin) || s.skin < SKIN_RAW_MIN || s.skin > SKIN_RAW_MAX) { *reason = "ntc_out_of_range"; return false; }
  const bool envOk = finite(s.env), humOk = finite(s.hum);
  if (envOk && (s.env < ENV_TEMP_MIN_C || s.env > ENV_TEMP_MAX_C)) { *reason = "env_temp_abnormal"; return false; }
  if (humOk && (s.hum < HUMIDITY_MIN_PCT || s.hum > HUMIDITY_MAX_PCT)) { *reason = "humidity_abnormal"; return false; }
  if (requireEnvironment && (!envOk || !humOk)) { *reason = "env_sensor_missing"; return false; }
  *reason = "ok";
  return true;
}

// ============================================================== window
void FeatureWindow::reset() {
  head_ = count_ = 0;
  restartBaseline();
}

void FeatureWindow::restartBaseline() {
  started_ = ready_ = false;
  t0_ = skinSum_ = humSum_ = 0.0;
  skinN_ = humN_ = 0;
}

double FeatureWindow::skinBaseline() const {
  if (finite(preset_)) return preset_;
  return (ready_ && skinN_ > 0) ? skinSum_ / skinN_ : NAN;
}

double FeatureWindow::humBaseline() const { return (ready_ && humN_ > 0) ? humSum_ / humN_ : NAN; }

void FeatureWindow::add(const Sample& s) {
  if (!started_) { started_ = true; t0_ = s.t; }
  if (!ready_) {
    if (s.t - t0_ <= BASELINE_S) {
      skinSum_ += s.skin; skinN_++;
      if (finite(s.hum)) { humSum_ += s.hum; humN_++; }
    }
    if (s.t - t0_ >= BASELINE_S && skinN_ > 0) ready_ = true;
  }
  if (count_ == kCapacity) { head_ = (head_ + 1) % kCapacity; count_--; }  // overflow guard
  buf_[(head_ + count_) % kCapacity] = s;
  count_++;
  while (count_ > 0 && s.t - at(0).t > WINDOW_S) { head_ = (head_ + 1) % kCapacity; count_--; }
}

// field: 0 skin, 1 env, 2 humidity. Least-squares slope in units per second.
void FeatureWindow::meanSlope(int field, double& mean, double& slope) const {
  mean = slope = NAN;
  const double tStart = at(0).t;
  long n = 0;
  double sumT = 0.0, sumY = 0.0;
  for (size_t i = 0; i < count_; i++) {
    const Sample& s = at(i);
    const double y = field == 0 ? s.skin : (field == 1 ? s.env : s.hum);
    if (finite(y)) { n++; sumT += s.t - tStart; sumY += y; }
  }
  if (n == 0) return;
  const double mt = sumT / n, my = sumY / n;
  mean = my;
  if (n < 2) return;
  double sxy = 0.0, sxx = 0.0;
  for (size_t i = 0; i < count_; i++) {
    const Sample& s = at(i);
    const double y = field == 0 ? s.skin : (field == 1 ? s.env : s.hum);
    if (finite(y)) {
      const double dt = (s.t - tStart) - mt;
      sxy += dt * (y - my);
      sxx += dt * dt;
    }
  }
  if (sxx > 0.0) slope = sxy / sxx;
}

bool FeatureWindow::features(Features& f) const {
  if (count_ < static_cast<size_t>(MIN_WINDOW_SAMPLES)) return false;
  meanSlope(0, f.skinMean, f.skinSlope);
  meanSlope(1, f.envMean, f.envSlope);
  meanSlope(2, f.humMean, f.humSlope);
  const double sb = skinBaseline(), hb = humBaseline();
  f.skinDelta = finite(sb) ? f.skinMean - sb : NAN;
  f.humRelative = (finite(f.humMean) && finite(hb)) ? (f.humMean - hb) / (std::fabs(hb) > 1.0 ? std::fabs(hb) : 1.0) : NAN;
  return true;
}

// ============================================================== Heat Score
Assessment scoreFeatures(const Features& f, double persistence) {
  const double d = SKIN_WARMING_DIRECTION;
  struct Comp { double signedValue, weight; int group; bool isTrend; };
  const Comp comps[6] = {
      {finite(f.skinDelta) ? d * f.skinDelta / SKIN_DELTA_SCALE_RAW : NAN, W_SKIN_DELTA, 0, false},
      {finite(f.skinSlope) ? d * f.skinSlope / SKIN_SLOPE_SCALE_RAW_PER_S : NAN, W_SKIN_SLOPE, 0, true},
      {finite(f.envMean) ? (f.envMean - ENV_REFERENCE_C) / ENV_LEVEL_SCALE_C : NAN, W_ENV_LEVEL, 1, false},
      {finite(f.envSlope) ? f.envSlope / ENV_SLOPE_SCALE_C_PER_S : NAN, W_ENV_SLOPE, 1, true},
      {finite(f.humRelative) ? f.humRelative / HUMIDITY_RELATIVE_SCALE : NAN, W_HUMIDITY_RELATIVE, 2, false},
      {finite(f.humSlope) ? f.humSlope / HUMIDITY_SLOPE_SCALE_PCT_PER_S : NAN, W_HUMIDITY_SLOPE, 2, true},
  };
  double num = 0.0, den = 0.0, tnum = 0.0, tden = 0.0, allW = 0.0;
  double groupMax[3] = {-1.0, -1.0, -1.0};  // -1 == group missing
  for (const Comp& c : comps) {
    allW += c.weight;
    if (!finite(c.signedValue)) continue;
    const double v = clip(c.signedValue);
    num += v * c.weight;
    den += c.weight;
    if (v > groupMax[c.group]) groupMax[c.group] = v;
    if (c.isTrend) { tnum += clip(c.signedValue, -1.0, 1.0) * c.weight; tden += c.weight; }
  }
  Assessment a;
  a.heat = den > 0.0 ? clip(num / den) : 0.0;
  a.trend = tden > 0.0 ? tnum / tden : 0.0;
  for (double m : groupMax) if (m >= EVIDENCE_COMPONENT_THRESHOLD) a.evidence++;
  const double coverage = allW > 0.0 ? den / allW : 0.0;
  double agreement = a.evidence == 0 ? 0.35 : 0.45 + 0.275 * a.evidence;
  if (agreement > 1.0) agreement = 1.0;
  const double baselineFactor = finite(f.skinDelta) ? 1.0 : 0.8;
  a.confidence = clip(coverage * (0.45 + 0.30 * clip(persistence) + 0.25 * agreement) * baselineFactor);
  a.baselineReady = finite(f.skinDelta);
  return a;
}

// ============================================================== state machine
void StateMachine::reset(double now) {
  state_ = State::STABLE;
  pwm_ = 0;
  entered_ = lastPowerChange_ = now;
  nEarly_ = nHigh_ = nRecovery_ = 0;
  faultReason_ = "";
}

double StateMachine::minHold() const {
  switch (state_) {
    case State::EARLY_HEAT: return MIN_HOLD_EARLY_S;
    case State::HIGH_HEAT_LOAD: return MIN_HOLD_HIGH_S;
    case State::RECOVERY: return MIN_HOLD_RECOVERY_S;
    case State::MAINTAIN: return MIN_HOLD_MAINTAIN_S;
    default: return 0.0;
  }
}

Decision StateMachine::go(State s, int pwm, const char* action, const char* reason, double now) {
  state_ = s;
  pwm_ = imax(0, imin(pwm, MAX_PWM));
  entered_ = lastPowerChange_ = now;
  nEarly_ = nHigh_ = nRecovery_ = 0;
  return {state_, pwm_, action, reason};
}

Decision StateMachine::fault(const char* reason, double now) {
  if (state_ == State::FAULT) return {State::FAULT, 0, "SAFETY_STOP", faultReason_};
  faultReason_ = reason;
  return go(State::FAULT, 0, "SAFETY_STOP", reason, now);
}

bool StateMachine::resetFault(double now) {
  if (state_ != State::FAULT) return false;
  faultReason_ = "";
  go(State::STABLE, 0, "RESET_FAULT", "manual_reset", now);
  return true;
}

Decision StateMachine::update(const Assessment& a, bool safe, const char* safetyReason, double now) {
  if (state_ == State::FAULT) return {State::FAULT, 0, "SAFETY_STOP", faultReason_};
  if (!safe) return fault(safetyReason, now);

  const bool high = a.heat >= HIGH_ENTER_SCORE && a.evidence >= HIGH_MIN_EVIDENCE && a.confidence >= HIGH_MIN_CONFIDENCE;
  const bool early = a.heat >= EARLY_ENTER_SCORE && a.trend >= EARLY_MIN_TREND && a.evidence >= EARLY_MIN_EVIDENCE &&
                     a.confidence >= EARLY_MIN_CONFIDENCE;
  const bool improving = a.trend <= RECOVERY_TREND || a.heat <= RECOVERY_ENTER_SCORE;
  const bool stable = a.heat <= STABLE_SCORE && std::fabs(a.trend) <= STABLE_ABS_TREND;
  nHigh_ = high ? nHigh_ + 1 : 0;
  nEarly_ = early ? nEarly_ + 1 : 0;
  nRecovery_ = improving ? nRecovery_ + 1 : 0;
  const bool highReady = nHigh_ >= HIGH_CONSECUTIVE;
  const bool earlyReady = nEarly_ >= EARLY_CONSECUTIVE;
  const bool recoveryReady = nRecovery_ >= RECOVERY_CONSECUTIVE;
  const double sincePower = now - lastPowerChange_;

  switch (state_) {
    case State::STABLE:
      if (highReady) return go(State::HIGH_HEAT_LOAD, HIGH_START_PWM, "DIRECT_HIGH", "high_score_multi_evidence", now);
      if (earlyReady) return go(State::EARLY_HEAT, EARLY_PWM, "START_LOW", "persistent_early_heat_trend", now);
      return {State::STABLE, 0, "HOLD_OFF", "stable_or_unconfirmed"};

    case State::EARLY_HEAT:
      if (highReady) return go(State::HIGH_HEAT_LOAD, HIGH_START_PWM, "ESCALATE", "heat_load_became_high", now);
      if (recoveryReady && held(now)) return go(State::RECOVERY, pwm_, "BEGIN_RAMP_DOWN", "trend_improving", now);
      if (early && sincePower >= POWER_STEP_S) {
        pwm_ = imax(0, imin(imin(pwm_ + PWM_STEP, HIGH_START_PWM), MAX_PWM));
        lastPowerChange_ = now;
        return {state_, pwm_, "STEP_UP", "early_heat_still_rising"};
      }
      break;

    case State::HIGH_HEAT_LOAD:
      if (recoveryReady && held(now)) return go(State::RECOVERY, pwm_, "BEGIN_RAMP_DOWN", "heat_trend_converging", now);
      if (high && a.trend > WORSENING_TREND && sincePower >= POWER_STEP_S) {
        pwm_ = imax(0, imin(pwm_ + PWM_STEP, MAX_PWM));
        lastPowerChange_ = now;
        return {state_, pwm_, "STEP_UP", "high_heat_still_rising"};
      }
      break;

    case State::RECOVERY:
      if (highReady) return go(State::HIGH_HEAT_LOAD, HIGH_START_PWM, "REESCALATE", "high_heat_returned", now);
      if (earlyReady) return go(State::EARLY_HEAT, imax(EARLY_PWM, pwm_), "REESCALATE", "early_heat_returned", now);
      if (improving && sincePower >= POWER_STEP_S) {
        pwm_ = imax(0, imin(imax(MAINTAIN_PWM, pwm_ - PWM_STEP), MAX_PWM));
        lastPowerChange_ = now;
        if (pwm_ <= MAINTAIN_PWM && a.heat <= MAINTAIN_ENTER_SCORE)
          return go(State::MAINTAIN, MAINTAIN_PWM, "LIGHT_MAINTAIN", "recovery_near_stable", now);
        return {state_, pwm_, "STEP_DOWN", "improving_lower_power"};
      }
      break;

    case State::MAINTAIN:
      if (highReady) return go(State::HIGH_HEAT_LOAD, HIGH_START_PWM, "REESCALATE", "high_heat_returned", now);
      if (earlyReady) return go(State::EARLY_HEAT, EARLY_PWM, "REESCALATE", "early_heat_returned", now);
      if (stable && held(now) && now - entered_ >= MAINTAIN_S)
        return go(State::STABLE, 0, "TURN_OFF", "stable_after_maintain", now);
      break;

    case State::FAULT:
      break;
  }
  return {state_, pwm_, "HOLD", "min_hold_or_no_condition"};
}

// ============================================================== actuator guard
void ActuatorGuard::reset() { on_ = cooling_ = false; onSince_ = cooldownUntil_ = 0.0; }

int ActuatorGuard::apply(int requested, double now, const char** guard) {
  if (requested <= 0) { on_ = false; *guard = "off"; return 0; }
  if (cooling_) {
    if (now < cooldownUntil_) { on_ = false; *guard = "cooldown"; return 0; }
    cooling_ = false;
  }
  int out = requested;
  *guard = "ok";
  if (out > MAX_PWM) { out = MAX_PWM; *guard = "clamped_max_duty"; }
  if (!on_) { on_ = true; onSince_ = now; }
  if (now - onSince_ >= MAX_ON_S) {
    cooling_ = true;
    cooldownUntil_ = now + COOLDOWN_S;
    on_ = false;
    *guard = "max_on_time_cooldown";
    return 0;
  }
  return out;
}

// ============================================================== response tracker
void ResponseTracker::reset() {
  started_ = false;
  startedAt_ = refScore_ = 0.0;
  lastPwm_ = nImproving_ = nRecovered_ = 0;
}

Response ResponseTracker::update(double heat, double trend, int pwm, State state, double now) {
  if (pwm <= 0) {
    const bool recovered = started_ && state == State::STABLE;
    started_ = false;
    lastPwm_ = nImproving_ = nRecovered_ = 0;
    return recovered ? Response::RECOVERED : Response::NOT_INTERVENING;
  }
  if (!started_ || pwm != lastPwm_) {
    started_ = true;
    startedAt_ = now;
    refScore_ = heat;
    lastPwm_ = pwm;
    nImproving_ = nRecovered_ = 0;
    return Response::WAITING_FOR_RESPONSE;
  }
  const double elapsed = now - startedAt_, change = heat - refScore_;
  if (elapsed < RESP_MIN_OBSERVATION_S) return Response::WAITING_FOR_RESPONSE;
  if (heat <= RESP_RECOVERED_SCORE && std::fabs(trend) <= STABLE_ABS_TREND) {
    if (++nRecovered_ >= RESP_RECOVERED_CONSECUTIVE) return Response::RECOVERED;
  } else {
    nRecovered_ = 0;
  }
  if (change <= -RESP_MIN_SCORE_CHANGE || trend <= -RESP_MIN_TREND) {
    if (++nImproving_ >= RESP_IMPROVING_CONSECUTIVE) return Response::IMPROVING;
  } else {
    nImproving_ = 0;
  }
  if (change >= RESP_MIN_SCORE_CHANGE && trend >= RESP_MIN_TREND) return Response::WORSENING;
  return Response::NO_CLEAR_RESPONSE;
}

// ============================================================== engine
void Engine::reset(double now) {
  window_.reset();
  sm_.reset(now);
  guard_.reset();
  resp_.reset();
  heatRun_ = 0;
}

Record Engine::step(const Sample& s) {
  Record r{};
  r.sample = s;
  const char* why = "ok";
  const bool safe = checkSample(s, requireEnv_, &why);
  Assessment a;
  if (finite(s.skin)) {
    window_.add(s);
    Features f;
    if (window_.features(f)) {
      a = scoreFeatures(f, 0.0);
      heatRun_ = a.heat >= EARLY_ENTER_SCORE ? imin(heatRun_ + 1, PERSISTENCE_WINDOWS) : 0;
      a = scoreFeatures(f, static_cast<double>(heatRun_) / PERSISTENCE_WINDOWS);
    }
  }
  r.skinBaseline = window_.skinBaseline();
  a.baselineReady = finite(r.skinBaseline);
  r.a = a;
  r.d = sm_.update(a, safe, why, s.t);
  if (r.d.state == State::FAULT) {
    const char* ignored;
    r.pwmOut = guard_.apply(0, s.t, &ignored);
    r.guard = "fault_off";
  } else {
    r.pwmOut = guard_.apply(r.d.pwm, s.t, &r.guard);
  }
  r.response = resp_.update(a.heat, a.trend, r.pwmOut, r.d.state, s.t);
  return r;
}

// ============================================================== CSV
const char* const kLogHeader =
    "t_s,skin_raw,env_temp,humidity,baseline_ready,skin_baseline,heat_score,trend_score,confidence,"
    "evidence,state,action,reason,pwm_req,pwm_out,guard,response";

int formatRecord(const Record& r, char* buf, size_t len) {
  char env[16] = "", hum[16] = "", base[16] = "";
  if (finite(r.sample.env)) snprintf(env, sizeof env, "%.1f", r.sample.env);
  if (finite(r.sample.hum)) snprintf(hum, sizeof hum, "%.1f", r.sample.hum);
  if (finite(r.skinBaseline)) snprintf(base, sizeof base, "%.2f", r.skinBaseline);
  return snprintf(buf, len, "%.3f,%.0f,%s,%s,%d,%s,%.4f,%.4f,%.4f,%d,%s,%s,%s,%d,%d,%s,%s", r.sample.t, r.sample.skin, env,
                  hum, r.a.baselineReady ? 1 : 0, base, r.a.heat, r.a.trend, r.a.confidence, r.a.evidence,
                  stateName(r.d.state), r.d.action, r.d.reason, r.d.pwm, r.pwmOut, r.guard, responseName(r.response));
}

}  // namespace flexrest
