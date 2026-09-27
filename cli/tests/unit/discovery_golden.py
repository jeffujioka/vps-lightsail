"""Discovery goldens (index R12): tests/unit/golden/*.json, compared with `sw` normalised."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
SW_PLACEHOLDER = "<__version__>"                 # spec-01 §5.2's own notation


def load_golden(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text())


def normalise_sw(doc: Mapping) -> dict:
    """A deep copy of a discovery document whose dev.sw and o.sw read SW_PLACEHOLDER."""
    out = copy.deepcopy(dict(doc))
    for part in ("dev", "o"):
        if isinstance(out.get(part), dict) and "sw" in out[part]:
            out[part]["sw"] = SW_PLACEHOLDER
    return out
