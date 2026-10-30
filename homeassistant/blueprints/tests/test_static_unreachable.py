"""spec-07 §3 / §8.2 specifics of blueprint A (unreachable.yaml)."""
from bp_loader import Input, declared_inputs, load_file

DOC = load_file("unreachable.yaml")
INPUTS = declared_inputs(DOC)

def test_detection_inputs():
    problem = INPUTS["problem_states"]
    assert problem["default"] == ["unreachable", "wg_down"]
    assert problem["selector"]["select"]["multiple"] is True
    assert [o["value"] for o in problem["selector"]["select"]["options"]] == [
        "unreachable", "wg_down", "error"]
    number = INPUTS["problem_minutes"]["selector"]["number"]
    assert (INPUTS["problem_minutes"]["default"], number["min"], number["max"]) == (10, 1, 240)
    assert INPUTS["ignore_during_operation"]["default"] is True
    assert INPUTS["send_recovery"]["default"] is True
    assert INPUTS["state_entity"]["default"] == "sensor.vps_lightsail_state"

def test_template_trigger_held_for_problem_minutes():
    (trigger,) = DOC["triggers"]
    assert trigger["trigger"] == "template"
    assert trigger["for"] == {"minutes": Input("problem_minutes")}
    for needle in ("underlying_state", "operation", "problem_states", "ignore_during_operation"):
        assert needle in trigger["value_template"], needle
    assert set(DOC["trigger_variables"]) == {"state_entity", "problem_states", "ignore_during_operation"}

def test_recovery_waits_under_send_recovery_and_reuses_the_notify_block():
    notify_all, recovery = DOC["actions"]
    assert recovery["if"] == "{{ send_recovery }}"
    wait, healthy = recovery["then"]
    template = wait["wait_for_trigger"][0]["value_template"]
    assert "['unavailable', 'unknown']" in template and "problem_states" in template
    assert "is_state(state_entity, 'healthy')" in healthy["if"]
    assert healthy["then"][-1] is notify_all  # the *notify_all alias survived the copy
    assert healthy["then"][0]["variables"]["interruption"] == "active"
    assert (DOC["variables"]["tag"], DOC["variables"]["interruption"]) == (
        "vps_lightsail_unreachable", "time-sensitive")
