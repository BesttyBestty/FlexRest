// Flex & Rest 翻个好觉 — ESP32-S3 firmware (SheNicest hackathon version)
//
// Sensing : NTC divider on GPIO1 -> skin_raw (12-bit ADC trend, not degC)
//           DHT11 on GPIO17      -> bed microclimate temperature / humidity
// Decision: 60 s window + personal baseline -> Heat Score -> graded state machine
// Output  : 8-bit PWM on GPIO10 -> logic-level N-MOSFET gate -> TEC (5 V supply)
//           Default build is SIMULATED: the pin is held LOW and the log shows
//           the PWM the controller *would* apply. Flip FLEXREST_SIMULATE_ACTUATOR
//           to 0 only after the bench checks in hardware_safety.md pass.
// Logging : one CSV line per second on USB serial (115200), header below.
//
// Serial commands (newline terminated):
//   STOP            latch FAULT(manual_stop), output off until RESET
//   RESET           clear a latched FAULT (only if the next sample is safe)
//   BASELINE        restart the 180 s personal baseline
//   BASELINE:<raw>  use a known personal skin_raw baseline immediately
//   HELP            list commands
#include <Arduino.h>
#include <DHT.h>

#include "params.h"
#include "thermal_core.h"

#ifndef FLEXREST_SIMULATE_ACTUATOR
#define FLEXREST_SIMULATE_ACTUATOR 1
#endif

namespace pins {
constexpr uint8_t NTC_ADC = 1;   // NTC + fixed resistor divider, ADC1_CH0
constexpr uint8_t DHT_DATA = 17; // DHT11 DATA (10 k pull-up to 3V3)
constexpr uint8_t TEC_PWM = 10;  // ~100 R to MOSFET gate, 10 k gate pull-down
}  // namespace pins

constexpr uint8_t PWM_CHANNEL = 0;
constexpr uint32_t PWM_FREQ_HZ = 5000;
constexpr uint8_t PWM_BITS = 8;
constexpr uint32_t SAMPLE_INTERVAL_MS = 1000;
constexpr uint32_t DHT_INTERVAL_MS = 2000;  // DHT11 cannot be read faster
constexpr int NTC_OVERSAMPLE = 16;

DHT dht(pins::DHT_DATA, DHT11);
flexrest::Engine engine(params::REQUIRE_ENVIRONMENT != 0);

uint32_t lastSampleMs = 0, lastDhtMs = 0, lastDhtOkMs = 0;
float lastEnv = NAN, lastHum = NAN;
bool dhtEverOk = false;
String rxLine;

static void writeGate(int duty) {
#if FLEXREST_SIMULATE_ACTUATOR
  (void)duty;
  duty = 0;  // simulated actuator: the MOSFET gate never turns on
#endif
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(pins::TEC_PWM, duty);
#else
  ledcWrite(PWM_CHANNEL, duty);
#endif
}

static double nowSeconds() { return millis() / 1000.0; }

static int readNtcRaw() {
  uint32_t sum = 0;
  for (int i = 0; i < NTC_OVERSAMPLE; i++) sum += analogRead(pins::NTC_ADC);
  return static_cast<int>((sum + NTC_OVERSAMPLE / 2) / NTC_OVERSAMPLE);
}

static void printHelp() {
  Serial.println(F("# commands: STOP | RESET | BASELINE | BASELINE:<skin_raw> | HELP"));
#if FLEXREST_SIMULATE_ACTUATOR
  Serial.println(F("# actuator: SIMULATED (gate held LOW; pwm_out = what would be applied)"));
#else
  Serial.println(F("# actuator: LIVE (TEC driven through MOSFET)"));
#endif
}

static void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.isEmpty()) return;
  const double t = nowSeconds();
  if (cmd == "STOP") {
    engine.stop(t);
    writeGate(0);
    Serial.println(F("# STOP -> FAULT(manual_stop), output off"));
  } else if (cmd == "RESET") {
    Serial.println(engine.resetFault(t) ? F("# RESET -> STABLE") : F("# RESET ignored (not in FAULT)"));
  } else if (cmd == "BASELINE") {
    engine.restartBaseline();
    Serial.println(F("# baseline restarted (180 s)"));
  } else if (cmd.startsWith("BASELINE:")) {
    const int raw = cmd.substring(9).toInt();
    if (raw >= params::SKIN_RAW_MIN && raw <= params::SKIN_RAW_MAX) {
      engine.setPresetBaseline(raw);
      Serial.printf("# preset skin baseline = %d\n", raw);
    } else {
      Serial.println(F("# BASELINE:<raw> out of range, ignored"));
    }
  } else if (cmd == "HELP") {
    printHelp();
  } else {
    Serial.println(F("# unknown command"));
  }
}

static void pollSerial() {
  while (Serial.available()) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      handleCommand(rxLine);
      rxLine = "";
    } else if (rxLine.length() < 48) {
      rxLine += c;
    }
  }
}

static void pollDht(uint32_t nowMs) {
  if (nowMs - lastDhtMs < DHT_INTERVAL_MS) return;
  lastDhtMs = nowMs;
  const float t = dht.readTemperature();
  const float h = dht.readHumidity();
  if (!isnan(t) && !isnan(h)) {
    lastEnv = t;
    lastHum = h;
    lastDhtOkMs = nowMs;
    dhtEverOk = true;
  }
}

void setup() {
  // Gate LOW before anything else: the TEC must be off through boot and reset.
  pinMode(pins::TEC_PWM, OUTPUT);
  digitalWrite(pins::TEC_PWM, LOW);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(pins::TEC_PWM, PWM_FREQ_HZ, PWM_BITS);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ_HZ, PWM_BITS);
  ledcAttachPin(pins::TEC_PWM, PWM_CHANNEL);
#endif
  writeGate(0);

  Serial.begin(115200);
  delay(1000);  // native USB + DHT11 settle time
  analogReadResolution(12);
  analogSetPinAttenuation(pins::NTC_ADC, ADC_11db);
  dht.begin();

  engine.reset(nowSeconds());
  Serial.println(F("# Flex & Rest firmware (hackathon version) — skin_raw is ADC trend, not degC"));
  printHelp();
  Serial.println(flexrest::kLogHeader);
}

void loop() {
  pollSerial();
  const uint32_t nowMs = millis();
  pollDht(nowMs);
  if (nowMs - lastSampleMs < SAMPLE_INTERVAL_MS) {
    delay(5);  // yield to background tasks
    return;
  }
  lastSampleMs = nowMs;

  const bool envFresh = dhtEverOk && (nowMs - lastDhtOkMs) <= static_cast<uint32_t>(params::ENV_STALE_S * 1000);
  flexrest::Sample s;
  s.t = nowMs / 1000.0;
  s.skin = readNtcRaw();
  s.env = envFresh ? lastEnv : NAN;
  s.hum = envFresh ? lastHum : NAN;

  // DHT11 needs a couple of seconds after power-up; don't latch a FAULT for that.
  if (!dhtEverOk && nowMs < 5000) return;

  const flexrest::Record r = engine.step(s);
  writeGate(r.pwmOut);

  char line[256];
  flexrest::formatRecord(r, line, sizeof line);
  Serial.println(line);
}
