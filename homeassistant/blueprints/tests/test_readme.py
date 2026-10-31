"""spec-07 §7.5 / acceptance 7: README links every blueprint and states the documented limits."""
from urllib.parse import quote

from bp_loader import BP_DIR

README = (BP_DIR.parents[1] / "README.md").read_text()
TEXT = " ".join(README.split())  # prose wraps across lines

def test_every_blueprint_has_an_import_badge():
    for path in sorted(BP_DIR.glob("*.yaml")):
        url = "https://github.com/guicheffer/vps-lightsail/blob/main/" + path.relative_to(
            BP_DIR.parents[3]).as_posix()
        assert "blueprint_url=" + quote(url, safe="") in README, path.name

def test_documented_behaviour_and_no_personal_data():
    for needle in ("one automation per threshold", "once per UTC month",
                   "only if the alert was sent", "restart during an outage"):
        assert needle in TEXT, needle
    assert "jeff" not in README.lower()
