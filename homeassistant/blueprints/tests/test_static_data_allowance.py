"""spec-07 §5 / §8.2 specifics of blueprint B (data_allowance.yaml)."""
from bp_loader import declared_inputs, load_file

DOC = load_file("data_allowance.yaml")
INPUTS = declared_inputs(DOC)

def test_threshold_and_source_inputs():
    number = INPUTS["threshold_pct"]["selector"]["number"]
    assert (INPUTS["threshold_pct"]["default"], number["min"], number["max"]) == (80, 1, 500)
    assert {k: INPUTS[k]["default"] for k in ("allowance_entity", "data_entity", "cost_entity")} == {
        "allowance_entity": "sensor.vps_lightsail_allowance_pct",
        "data_entity": "sensor.vps_lightsail_data_total",
        "cost_entity": "sensor.vps_lightsail_cost_projected"}

def test_crossing_trigger_treats_unavailable_as_minus_one():
    (trigger,) = DOC["triggers"]
    assert trigger["trigger"] == "template" and "for" not in trigger
    assert "float(-1)" in trigger["value_template"] and "threshold_pct" in trigger["value_template"]

def test_once_per_utc_month_dedupe():
    (condition,) = DOC["conditions"]
    template = condition["value_template"]
    assert condition["condition"] == "template"
    assert "this.attributes.last_triggered" in template
    assert "timestamp_custom('%Y-%m', false)" in template and "utcnow().strftime('%Y-%m')" in template

def test_tag_per_threshold_and_no_recovery_wait():
    assert DOC["variables"]["tag"] == "vps_lightsail_allowance_{{ threshold_pct | int }}"
    assert "wait_for_trigger" not in str(DOC["actions"])
