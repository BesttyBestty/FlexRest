// Host replay harness: feeds a CSV (t_s,skin_raw,env_temp,humidity[,...]) through
// the exact firmware core (thermal_core.cpp) and prints the firmware log.
// Used by test_firmware_parity.py; build manually with:
//   c++ -std=c++17 -O2 -I. host_replay.cpp thermal_core.cpp -o replay
//   ./replay data_2026-08-29_evening_labeled.csv
// Options: --allow-missing-env   (skin-only nights without DHT11)
//          --gap-reset <s>       (re-initialise like a reboot after a gap, default 10)
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "thermal_core.h"

static double field(const std::string& s) {
  if (s.empty()) return NAN;
  char* end = nullptr;
  const double v = std::strtod(s.c_str(), &end);
  return end == s.c_str() ? NAN : v;
}

int main(int argc, char** argv) {
  const char* path = nullptr;
  bool requireEnv = true;
  double gapReset = 10.0;
  for (int i = 1; i < argc; i++) {
    if (!std::strcmp(argv[i], "--allow-missing-env")) requireEnv = false;
    else if (!std::strcmp(argv[i], "--gap-reset") && i + 1 < argc) gapReset = std::atof(argv[++i]);
    else path = argv[i];
  }
  if (!path) { std::fprintf(stderr, "usage: replay [--allow-missing-env] [--gap-reset s] file.csv\n"); return 2; }
  FILE* fp = std::fopen(path, "r");
  if (!fp) { std::perror(path); return 1; }

  flexrest::Engine engine(requireEnv);
  char line[1024], out[512];
  bool header = true, first = true;
  double lastT = 0.0;
  std::printf("%s\n", flexrest::kLogHeader);
  while (std::fgets(line, sizeof line, fp)) {
    if (header) { header = false; continue; }  // expects t_s,skin_raw,env_temp,humidity first
    std::string cols[4];
    int c = 0;
    for (char* p = line; *p && *p != '\n' && *p != '\r' && c < 4; p++) {
      if (*p == ',') c++;
      else cols[c] += *p;
    }
    flexrest::Sample s{field(cols[0]), field(cols[1]), field(cols[2]), field(cols[3])};
    if (!std::isfinite(s.t)) continue;
    if (first || s.t - lastT > gapReset) engine.reset(s.t);
    first = false;
    lastT = s.t;
    const flexrest::Record r = engine.step(s);
    flexrest::formatRecord(r, out, sizeof out);
    std::printf("%s\n", out);
  }
  std::fclose(fp);
  return 0;
}
