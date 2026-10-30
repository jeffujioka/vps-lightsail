# homeassistant/blueprints/tests/test_production.py
"""Offline tests for production.py (spec-07 §7.3 bodies, §8.4 smoke rules)."""
import re

from production import ENTITIES, config_problems, production_instances, state_problems

DEV = "0123456789abcdef0123456789abcdef"

def test_bodies_match_spec_7_3_with_two_allowance_instances():
    inst = production_instances([DEV])
    assert list(inst) == ["vps_lightsail_unreachable", "vps_lightsail_allowance_80",
                          "vps_lightsail_allowance_100", "vps_lightsail_agent_offline"]
    assert inst["vps_lightsail_unreachable"] == {
        "alias": "VPS Lightsail: unreachable",
        "description": "Blueprint vps_lightsail/unreachable.yaml (spec-07)",
        "use_blueprint": {"path": "vps_lightsail/unreachable.yaml", "input": {"notify_devices": [DEV]}}}
    assert [inst[c]["use_blueprint"]["input"].get("threshold_pct") for c in inst] == [None, 80, 100, None]
    assert inst["vps_lightsail_agent_offline"]["use_blueprint"]["path"] == "vps_lightsail/agent_offline.yaml"
    for config_id, body in inst.items():  # entity id = slug of the alias = automation.<config id>
        assert re.sub(r"[^a-z0-9]+", "_", body["alias"].lower()).strip("_") == config_id

def test_unreachable_minutes_only_changes_the_unreachable_instance():
    inst = production_instances([DEV], unreachable_minutes=1)
    assert inst["vps_lightsail_unreachable"]["use_blueprint"]["input"]["problem_minutes"] == 1
    assert all("problem_minutes" not in b["use_blueprint"]["input"]
               for c, b in inst.items() if c != "vps_lightsail_unreachable")

def test_state_rules_accept_unknown_metrics_but_never_unavailable():
    ok = dict(zip(ENTITIES, ({"state": "nonexistent"}, {"state": "unknown"}, {"state": "0.0"},
                            {"state": "unknown"})))
    assert state_problems(ok) == []
    assert state_problems({**ok, ENTITIES[0]: {"state": "unknown"}}) == [f"{ENTITIES[0]}: unknown"]
    assert state_problems({**ok, ENTITIES[2]: {"state": "unavailable"}}) == [f"{ENTITIES[2]}: unavailable"]
    assert state_problems({**ok, ENTITIES[3]: None}) == [f"{ENTITIES[3]}: missing"]

def test_config_check_flags_a_leftover_test_setting_and_a_missing_instance():
    expected = production_instances([DEV])
    stored = {c: {"id": c, **b} for c, b in production_instances([DEV], unreachable_minutes=1).items()}
    assert [p.split(":")[0] for p in config_problems(expected, stored)] == ["vps_lightsail_unreachable"]
    assert config_problems(expected, {c: {"id": c, **b} for c, b in expected.items()}) == []
    assert config_problems(expected, {})[0].startswith("vps_lightsail_unreachable: stored nothing")
