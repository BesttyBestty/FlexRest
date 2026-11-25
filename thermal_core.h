// Flex & Rest — hardware-independent thermal-state core.
//
// Pure C++ (no Arduino headers) so the exact same file runs on the ESP32-S3
// and in the host replay harness (host_replay.cpp), which the Python
// tests compare against flexrest_engine.py.
//
// Pipeline per 1 Hz sample:
//   safety check -> 60 s window + personal baseline -> Heat Score / Trend /
//   Confidence -> graded state machine -> actuator guard -> response tracker
#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace flexrest {

enum class State : uint8_t { STABLE, EARLY_HEAT, HIGH_HEAT_LOAD, RECOVERY, MAINTAIN, FAULT };
enum class Response : uint8_t { NOT_INTERVENING, WAITING_FOR_RESPONSE, IMPROVING, NO_CLEAR_RESPONSE, WORSENING, RECOVERED };

const char* stateName(State s);
const char* responseName(Response r);

struct Sample {
  double t;     // seconds since boot
  double skin;  // NTC raw ADC (0..4095), trend only
  double env;   // bed microclimate temperature, degC (NAN if missing/stale)
  double hum;   // bed microclimate relative humidity, % (NAN if missing/stale)
};

struct Features {
  double skinMean, skinSlope, skinDelta;  // NAN == missing
  double envMean, envSlope;
  double humMean, humSlope, humRelative;
};

struct Assessment {
  double heat = 0.0;        // 0..1, how strongly the evidence says "warming"
  double trend = 0.0;       // -1..1, direction of change (positive == warming)
  double confidence = 0.0;  // 0..1, coverage x persistence x agreement
  int evidence = 0;         // independent groups (skin, bed temp, humidity) >= threshold
  bool baselineReady = false;
};

struct Decision {
  State state;
  int pwm;
  const char* action;
  const char* reason;
};

// ---------------------------------------------------------------- window
class FeatureWindow {
 public:
  static constexpr size_t kCapacity = 256;  // > 60 s at 1 Hz with jitter
  void reset();
  void restartBaseline();
  void setPresetBaseline(double skinRaw) { preset_ = skinRaw; }
  void add(const Sample& s);
  bool features(Features& out) const;  // false until MIN_WINDOW_SAMPLES
  double skinBaseline() const;         // NAN until ready
  double humBaseline() const;
  size_t size() const { return count_; }

 private:
  const Sample& at(size_t i) const { return buf_[(head_ + i) % kCapacity]; }
  void meanSlope(int field, double& mean, double& slope) const;
  Sample buf_[kCapacity];
  size_t head_ = 0, count_ = 0;
  bool started_ = false, ready_ = false;
  double t0_ = 0.0, skinSum_ = 0.0, humSum_ = 0.0;
  long skinN_ = 0, humN_ = 0;
  double preset_ = NAN;
};

Assessment scoreFeatures(const Features& f, double persistence);
bool checkSample(const Sample& s, bool requireEnvironment, const char** reason);

// ---------------------------------------------------------------- state machine
class StateMachine {
 public:
  void reset(double now);
  Decision update(const Assessment& a, bool safe, const char* safetyReason, double now);
  Decision fault(const char* reason, double now);
  bool resetFault(double now);
  State state() const { return state_; }

 private:
  Decision go(State s, int pwm, const char* action, const char* reason, double now);
  double minHold() const;
  bool held(double now) const { return now - entered_ >= minHold(); }
  State state_ = State::STABLE;
  int pwm_ = 0;
  double entered_ = 0.0, lastPowerChange_ = 0.0;
  int nEarly_ = 0, nHigh_ = 0, nRecovery_ = 0;
  const char* faultReason_ = "";
};

// ---------------------------------------------------------------- actuator guard
class ActuatorGuard {
 public:
  void reset();
  int apply(int requested, double now, const char** guard);

 private:
  bool on_ = false, cooling_ = false;
  double onSince_ = 0.0, cooldownUntil_ = 0.0;
};

// ---------------------------------------------------------------- response tracker
class ResponseTracker {
 public:
  void reset();
  Response update(double heat, double trend, int pwm, State state, double now);

 private:
  bool started_ = false;
  double startedAt_ = 0.0, refScore_ = 0.0;
  int lastPwm_ = 0, nImproving_ = 0, nRecovered_ = 0;
};

// ---------------------------------------------------------------- whole pipeline
struct Record {
  Sample sample;
  Assessment a;
  double skinBaseline;
  Decision d;
  int pwmOut;
  const char* guard;
  Response response;
};

class Engine {
 public:
  explicit Engine(bool requireEnvironment = true) : requireEnv_(requireEnvironment) { reset(0.0); }
  void reset(double now);
  void setPresetBaseline(double skinRaw) { window_.setPresetBaseline(skinRaw); }
  void restartBaseline() { window_.restartBaseline(); }
  void stop(double now) { sm_.fault("manual_stop", now); }
  bool resetFault(double now) { return sm_.resetFault(now); }
  Record step(const Sample& s);

 private:
  bool requireEnv_;
  FeatureWindow window_;
  StateMachine sm_;
  ActuatorGuard guard_;
  ResponseTracker resp_;
  int heatRun_ = 0;
};

// CSV helpers shared by firmware and host harness.
extern const char* const kLogHeader;
int formatRecord(const Record& r, char* buf, size_t len);

}  // namespace flexrest
