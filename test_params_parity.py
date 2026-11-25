"""The firmware constants and the Python simulator must be identical."""
import re

from flexrest_params import Params
from conftest import REPO


def test_params_header_matches_python():
    text = (REPO / "params.h").read_text()
    fw = {m.group(2): float(m.group(3)) for m in
          re.finditer(r"constexpr\s+(int|double)\s+(\w+)\s*=\s*([-0-9.eE]+);", text)}
    py = {n.upper(): float(getattr(Params(), n)) for n in Params.names()}
    assert fw == py
