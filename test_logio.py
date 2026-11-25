from flexrest_engine import LOG_COLUMNS
from flexrest_logio import parse_firmware_log

HEADER = ",".join(LOG_COLUMNS)
LINE = "12.000,600,26.5,55.0,0,,0.0000,0.0000,0.0000,0,STABLE,HOLD_OFF,stable_or_unconfirmed,0,0,off,NOT_INTERVENING"


def test_parser_handles_comments_reboots_and_garbage():
    text = "\n".join([
        "garbage before boot",
        "# Flex & Rest firmware",
        HEADER, LINE, "12.5,60",  # truncated line
        "# STOP -> FAULT",
        HEADER, LINE.replace("12.000", "1.000"),
    ])
    rows = parse_firmware_log(text)
    assert len(rows) == 2
    assert [r["boot"] for r in rows] == [0, 1]
    assert rows[0]["skin_raw"] == 600 and rows[0]["skin_baseline"] is None
    assert rows[0]["state"] == "STABLE" and rows[0]["pwm_out"] == 0
