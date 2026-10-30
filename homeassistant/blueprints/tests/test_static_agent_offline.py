"""spec-07 §6 / §8.2 specifics of blueprint C (agent_offline.yaml)."""
from bp_loader import Input, declared_inputs, load_file

DOC = load_file("agent_offline.yaml")
INPUTS = declared_inputs(DOC)

def test_offline_minutes_default_15():
    number = INPUTS["offline_minutes"]["selector"]["number"]
    assert (INPUTS["offline_minutes"]["default"], number["min"], number["max"]) == (15, 1, 1440)
    assert INPUTS["send_recovery"]["default"] is True

def test_state_trigger_to_unavailable_for_offline_minutes():
    (trigger,) = DOC["triggers"]
    assert trigger == {"trigger": "state", "id": "offline", "entity_id": Input("state_entity"),
                       "to": "unavailable", "for": {"minutes": Input("offline_minutes")}}

def test_back_online_waits_from_unavailable_but_not_to_unknown():
    notify_all, recovery = DOC["actions"]
    assert recovery["if"] == "{{ send_recovery }}"
    wait, variables, notify_again = recovery["then"]
    assert wait["wait_for_trigger"] == [{"trigger": "state", "entity_id": Input("state_entity"),
                                         "from": "unavailable", "not_to": "unknown"}]
    assert variables["variables"]["title"] == "VPS Lightsail: agent back online"
    assert notify_again is notify_all
    assert DOC["variables"]["tag"] == "vps_lightsail_agent_offline"
