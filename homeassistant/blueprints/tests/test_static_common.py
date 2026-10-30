"""spec-07 §8.2 checks shared by every blueprint (parametrised; 0 files = all skipped)."""
import re
from pathlib import Path

import pytest

from bp_loader import BP_DIR, Input, declared_inputs, load, used_inputs, walk

FILES = sorted(BP_DIR.glob("*.yaml"))
REPO_BLOB = "https://github.com/guicheffer/vps-lightsail/blob/main/"
parametrize = pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])

def test_loader_resolves_input_tags_sections_and_aliases():
    doc = load("blueprint:\n  input:\n    sec:\n      input:\n        a: {default: 1, selector: {text: {}}}\n"
               "    b: {default: 2, selector: {text: {}}}\ntriggers:\n  - &t {trigger: state, entity_id: !input a}\n"
               "actions:\n  - *t\nvariables: {x: !input b}\n")
    assert sorted(declared_inputs(doc)) == ["a", "b"]
    assert used_inputs(doc) == {"a", "b"}
    assert Input("a") in list(walk(doc))
    assert doc["actions"][0] is doc["triggers"][0]

@parametrize
def test_metadata_and_source_url(path: Path):
    bp = load(path.read_text())["blueprint"]
    assert bp["domain"] == "automation"
    assert bp["name"].startswith("VPS Lightsail:")
    assert bp["homeassistant"]["min_version"] == "2024.10.0"
    assert bp["source_url"] == REPO_BLOB + path.relative_to(BP_DIR.parents[3]).as_posix()

@parametrize
def test_inputs_in_sections_declared_used_and_defaulted(path: Path):
    doc = load(path.read_text())
    assert all("input" in section for section in doc["blueprint"]["input"].values())
    declared = declared_inputs(doc)
    assert used_inputs(doc) == set(declared)  # nothing undeclared, nothing unused
    for name, spec in declared.items():
        assert "default" in spec and "selector" in spec, name

@parametrize
def test_single_mode_and_modern_syntax(path: Path):
    doc = load(path.read_text())
    assert (doc["mode"], doc["max_exceeded"]) == ("single", "silent")
    assert {"triggers", "actions"} <= doc.keys()
    assert not {"trigger", "condition", "action"} & doc.keys()
    keys = {key for node in walk(doc) if isinstance(node, dict) for key in node}
    assert not {"service", "platform"} & keys

@parametrize
def test_no_hardcoded_action_and_no_personal_data(path: Path):
    text = path.read_text()
    assert re.findall(r"notify\.mobile_app_\w", text) == []  # only 'notify.mobile_app_{{ … }}'
    assert re.findall(r"\b[0-9a-f]{32}\b", text) == []  # no device ids (acceptance 7)
    assert "jeff" not in text.lower()

@parametrize
def test_three_notification_targets(path: Path):
    inputs = declared_inputs(load(path.read_text()))
    dev = inputs["notify_devices"]["selector"]["device"]
    ent = inputs["notify_entities"]["selector"]["entity"]
    assert dev["multiple"] is True and dev["filter"] == [{"integration": "mobile_app"}]
    assert ent["multiple"] is True and ent["filter"] == [{"domain": "notify"}]
    assert inputs["notify_action"]["selector"] == {"text": {}}
    assert [inputs[k]["default"] for k in ("notify_devices", "notify_entities", "notify_action")] == [[], [], ""]
