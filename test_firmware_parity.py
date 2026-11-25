"""Compile thermal_core.cpp for the host and compare with Python."""
import csv
import io
import shutil
import subprocess

import pytest

from flexrest_engine import LOG_COLUMNS
from flexrest_logio import firmware_style, read_session_csv, write_rows
from flexrest_replay import replay
from conftest import REPO
from synth import HEAT_EPISODE, night

CXX = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")


@pytest.fixture(scope="module")
def replay_bin(tmp_path_factory):
    if not CXX:
        pytest.skip("no C++ compiler on PATH")
    out = tmp_path_factory.mktemp("fw") / "replay"
    subprocess.run([CXX, "-std=c++17", "-O2", f"-I{REPO}", str(REPO / "host_replay.cpp"),
                    str(REPO / "thermal_core.cpp"), "-o", str(out)], check=True)
    return out


def _compare(replay_bin, csv_path, extra=(), **kw):
    proc = subprocess.run([str(replay_bin), *extra, str(csv_path)], check=True, capture_output=True, text=True)
    fw = list(csv.DictReader(io.StringIO(proc.stdout)))
    py = replay(read_session_csv(csv_path), **kw)
    assert len(fw) == len(py)
    for a, b in zip(fw, py):
        assert a["state"] == b["state"], (a, b)
        assert a["action"] == b["action"]
        assert int(a["pwm_out"]) == b["pwm_out"]
        assert a["guard"] == b["guard"]
        assert a["response"] == b["response"]
        assert int(a["evidence"]) == b["evidence"]
        assert abs(float(a["heat_score"]) - b["heat_score"]) < 1e-3
        assert abs(float(a["confidence"]) - b["confidence"]) < 1e-3


def test_parity_synthetic(replay_bin, tmp_path):
    path = tmp_path / "synthetic.csv"
    write_rows(night(HEAT_EPISODE), path, ["t_s", "skin_raw", "env_temp", "humidity"])
    _compare(replay_bin, path)


def test_parity_real_evening(replay_bin):
    _compare(replay_bin, REPO / "data_2026-08-29_evening_labeled.csv")


def test_parity_real_night_skin_only(replay_bin):
    _compare(replay_bin, REPO / "data_2026-08-29_night_skin-only.csv",
             extra=["--allow-missing-env"], allow_missing_env=True)


def test_csv_text_identical_on_real_evening(replay_bin):
    path = REPO / "data_2026-08-29_evening_labeled.csv"
    fw = subprocess.run([str(replay_bin), str(path)], check=True, capture_output=True, text=True).stdout
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=LOG_COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in replay(read_session_csv(path)):
        w.writerow({k: "" if v is None else v for k, v in firmware_style(r).items()})
    assert buf.getvalue() == fw
